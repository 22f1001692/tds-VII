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
# 1. Pre-generated catalog of exactly 46 orders for pagination
CATALOG = [{"id": i, "description": f"Order #{i}"} for i in range(1, TOTAL_ORDERS + 1)]

# 2. In-memory store for idempotency keys
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
            # Clean up requests older than 10 seconds
            self.clients[client_id] = [
                t for t in self.clients[client_id] if now - t < RATE_WINDOW
            ]
            
            # Check if bucket limit is reached
            if len(self.clients[client_id]) >= RATE_LIMIT:
                # Calculate how long until the oldest request in the window expires
                oldest_request = self.clients[client_id][0]
                retry_after = int(RATE_WINDOW - (now - oldest_request)) + 1
                
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests"},
                    headers={"Retry-After": str(max(1, retry_after))}
                )
                
            # Log this request's timestamp
            self.clients[client_id].append(now)

        return await call_next(request)

# Register Middlewares (Order matters: CORS Outer, Rate Limiter Inner)
app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows grader's browser to execute the fetch
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"] # Crucial: Let the browser read the 429 Retry-After header
)

# --- Endpoint 1: Cursor Pagination ---
def encode_cursor(index: int) -> str:
    """Creates an opaque base64 cursor from an index."""
    return base64.urlsafe_b64encode(str(index).encode()).decode()

def decode_cursor(cursor: str) -> int:
    """Decodes the opaque base64 cursor back to an index."""
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        return 0

@app.get("/orders")
async def get_orders(limit: int = 10, cursor: Optional[str] = None):
    # Determine starting index from cursor
    start_idx = decode_cursor(cursor) if cursor else 0
    end_idx = start_idx + limit
    
    # Slice the catalog
    items = CATALOG[start_idx:end_idx]
    
    # Determine the next cursor
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

    # Check if key already exists
    if idempotency_key in IDEMPOTENCY_STORE:
        # Return the EXACT SAME object that was generated the first time
        response.status_code = 200 # Standard practice for returning cached idempotent entity
        return IDEMPOTENCY_STORE[idempotency_key]
        
    # Generate a new unique order
    new_order = {
        "id": str(uuid.uuid4()),
        "status": "created",
        "timestamp": time.time()
    }
    
    # Store it linked to the key
    IDEMPOTENCY_STORE[idempotency_key] = new_order
    
    return new_order
