import time
import uuid
import base64
from typing import Optional
from collections import defaultdict

from fastapi import FastAPI, Request, Response, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# --- Configurations Assigned ---
TOTAL_ORDERS = 46
RATE_LIMIT = 18
RATE_WINDOW = 10  # seconds

# --- Data Stores ---
CATALOG = [{"id": i, "description": f"Order #{i}"} for i in range(1, TOTAL_ORDERS + 1)]
IDEMPOTENCY_STORE = {}

app = FastAPI()

# --- Middleware: Rate Limiting ---
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.clients = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        client_id = request.headers.get("x-client-id")
        
        if client_id:
            now = time.time()
            
            # 1. Add current request FIRST to prevent async parallel race conditions
            self.clients[client_id].append(now)
            
            # 2. Clean up requests older than the window
            self.clients[client_id] = [
                t for t in self.clients[client_id] if now - t < RATE_WINDOW
            ]
            
            # 3. Check if bucket limit is reached (using > because we already appended)
            if len(self.clients[client_id]) > RATE_LIMIT:
                oldest_request = self.clients[client_id][0]
                retry_after = int(RATE_WINDOW - (now - oldest_request)) + 1
                
                # Fix: Manually inject CORS headers on intercepted responses 
                origin = request.headers.get("origin", "*")
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests"},
                    headers={
                        "Retry-After": str(max(1, retry_after)),
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Expose-Headers": "Retry-After"
                    }
                )

        return await call_next(request)

# Register Middlewares 
app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False, # Changed to False to prevent wildcard (*) conflicts
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"]
)

# --- Endpoint 1: Cursor Pagination ---
def encode_cursor(index: int) -> str:
    return base64.urlsafe_b64encode(str(index).encode()).decode()

def decode_cursor(cursor: str) -> int:
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        return 0

@app.get("/orders")
async def get_orders(limit: int = 10, cursor: Optional[str] = None):
    start_idx = decode_cursor(cursor) if cursor else 0
    end_idx = start_idx + limit
    
    items = CATALOG[start_idx:end_idx]
    
    next_cursor = None
    if end_idx < TOTAL_ORDERS:
        next_cursor = encode_cursor(end_idx)
        
    return {
        "items": items,
        "next_cursor": next_cursor
    }

# --- Endpoint 2: Idempotent Order Creation ---
@app.post("/orders", status_code=201)
async def create_order(
    response: Response, 
    idempotency_key: str = Header(None, alias="Idempotency-Key")
):
    if not idempotency_key:
        return JSONResponse(
            status_code=400, 
            content={"detail": "Idempotency-Key header is missing."}
        )

    if idempotency_key in IDEMPOTENCY_STORE:
        response.status_code = 200
        return IDEMPOTENCY_STORE[idempotency_key]
        
    new_order = {
        "id": str(uuid.uuid4()),
        "status": "created",
        "timestamp": time.time()
    }
    
    IDEMPOTENCY_STORE[idempotency_key] = new_order
    
    return new_order
