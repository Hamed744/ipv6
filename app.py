import requests
from requests.adapters import HTTPAdapter
import random
import json
import time
import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware # Required for cross-origin if frontend is separate
from pydantic import BaseModel

# --- Global Variables for IPv6 Rotation ---
IPV6_ADDRESSES = []
current_ipv6_index = 0
IPV6_LIST_FILE = "/app/ipv6_ips.txt" # Path inside the Docker container

# --- FastAPI App Setup ---
app = FastAPI(
    title="Alpha Image Generator with IPv6 Rotation",
    description="Generates images using Flux Pro on Hugging Face Spaces with IPv6 IP rotation.",
    version="1.0.0"
)

# CORS Middleware (important if your frontend is on a different domain, though here it's served from the same app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Adjust this in production to specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Custom HTTP Adapter for Source IP Binding ---
class SourceAddressAdapter(HTTPAdapter):
    def __init__(self, source_address, **kwargs):
        self.source_address = source_address
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False):
        # We need to import urllib3 explicitly to access PoolManager
        import urllib3
        self.poolmanager = urllib3.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            source_address=(self.source_address, 0) # Specify the source IP here
        )

# --- Function to load IPv6 addresses from the file ---
def load_ipv6_addresses():
    global IPV6_ADDRESSES
    global current_ipv6_index
    if os.path.exists(IPV6_LIST_FILE):
        with open(IPV6_LIST_FILE, 'r') as f:
            IPV6_ADDRESSES = [line.strip() for line in f if line.strip()]
        print(f"Loaded {len(IPV6_ADDRESSES)} IPv6 addresses from {IPV6_LIST_FILE}")
        # Shuffle IPs to ensure better distribution if multiple concurrent requests happen
        random.shuffle(IPV6_ADDRESSES) 
        current_ipv6_index = 0
    else:
        print(f"WARNING: IPv6 address list file not found at {IPV6_LIST_FILE}. IPv6 rotation might not work.")
    if not IPV6_ADDRESSES:
        print("CRITICAL: No IPv6 addresses loaded. Falling back to default network behavior (no rotation).")

# Call this once when the application starts
@app.on_event("startup")
async def startup_event():
    load_ipv6_addresses()

# --- Custom Exception for GPU Quota Errors ---
class GPUQuotaError(Exception):
    """Custom exception for GPU quota errors."""
    def __init__(self, message="GPU quota exceeded or server busy. Please try again or with a new IP."):
        self.message = message
        super().__init__(self.message)

# --- Gradio API Configuration ---
IMAGE_GENERATOR_API_BASE_URL = "https://black-forest-labs-flux-1-dev.hf.space"
IMAGE_GENERATOR_FN_INDEX = 2
IMAGE_GENERATOR_TRIGGER_ID = 5

TRANSLATOR_API_BASE_URL = "https://hamed744-translate-tts-aloha.hf.space"
TRANSLATOR_FN_INDEX = 1
TRANSLATOR_OTHER_PARAMS = ["انگلیسی (آمریکا) - جنی (زن)", 0, 0, 0]

DEFAULT_RANDOMIZE_SEED = True
DEFAULT_GUIDANCE_SCALE = 3.5
DEFAULT_INFERENCE_STEPS = 28

PREDEFINED_DIMENSIONS_MAP = {
    "1:1": {"width": 1024, "height": 1024},
    "16:9": {"width": 1344, "height": 768},
    "9:16": {"width": 768, "height": 1344},
    "4:3":  {"width": 1152, "height": 864},
}

# --- Utility functions for Gradio API calls ---
def generate_session_hash():
    return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=26))

async def call_gradio_api_with_ipv6(base_url: str, fn_index: int, data_payload: list, session_hash: str, trigger_id: int = None, source_ip: str = None):
    join_url = f"{base_url}/gradio_api/queue/join"
    api_payload = {
        "data": data_payload,
        "event_data": None,
        "fn_index": fn_index,
        "session_hash": session_hash,
    }
    if trigger_id is not None:
        api_payload["trigger_id"] = trigger_id

    s = requests.Session()
    if source_ip and source_ip in IPV6_ADDRESSES:
        s.mount('https://', SourceAddressAdapter(source_ip))
        print(f"DEBUG: Using source IP {source_ip} for {base_url}")
    else:
        print(f"DEBUG: No specific source IP provided or found for {base_url}. Using default network.")

    try:
        response = s.post(join_url, headers={'Content-Type': 'application/json'}, json=api_payload, timeout=90) # Increased timeout
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        result = response.json()

        if not result.get("event_id") and result.get("error"):
            error_msg = result.get("error", "Unknown error from /queue/join.")
            error_msg_lower = error_msg.lower()
            if any(kw in error_msg_lower for kw in ["cuda", "gpu", "quota", "capacity", "load", "queue full", "too many requests", "rate limit"]):
                raise GPUQuotaError(f"Server (GPU) resource limit encountered: {error_msg[:150]}")
            raise ValueError(f"Error from /queue/join: {error_msg}")
        if not result.get("event_id"):
            raise ValueError("event_id not received from /queue/join.")
        return result["event_id"]
    except requests.exceptions.Timeout:
        raise ConnectionError(f"Connection to {base_url} timed out after 90 seconds.")
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        error_detail_lower = error_detail.lower()
        if any(kw in error_detail_lower for kw in ["cuda", "gpu", "quota", "capacity", "load", "queue full", "too many requests", "rate limit"]):
            raise GPUQuotaError(f"Server (GPU) resource limit encountered: {error_detail[:150]}")
        raise ConnectionError(f"Error connecting to {base_url}: {error_detail[:100]}")

async def poll_gradio_sse_status(base_url: str, session_hash: str, event_id: str, service_name: str, source_ip: str = None, max_poll_time: int = 300):
    """
    Polls the Gradio /queue/data endpoint to get the processing status.
    This replaces a true SSE client for simpler backend implementation.
    """
    s = requests.Session()
    if source_ip and source_ip in IPV6_ADDRESSES:
        s.mount('https://', SourceAddressAdapter(source_ip))

    data_url = f"{base_url}/gradio_api/queue/data?session_hash={session_hash}"
    start_time = time.time()

    last_progress_update = time.time()
    while time.time() - start_time < max_poll_time:
        try:
            response = s.get(data_url, timeout=30) # Short timeout for polling
            response.raise_for_status()
            
            # Gradio's /queue/data can return multiple lines of JSON, sometimes partial.
            # We need to split by newline and parse each complete JSON object.
            events = response.text.strip().split('\n')
            
            # Process events from latest to earliest, or collect all
            processed_events = []
            for event_str in events:
                if event_str.startswith('data:'):
                    try:
                        event_data = json.loads(event_str[len('data:'):].strip())
                        processed_events.append(event_data)
                    except json.JSONDecodeError:
                        print(f"WARNING: Could not decode JSON from SSE data: {event_str}")
                        continue
            
            # Look for the relevant events from the collected data
            for event_data in processed_events:
                if event_data.get("event_id") == event_id:
                    if event_data.get("msg") == "process_completed":
                        if event_data.get("success") and event_data.get("output") and event_data.get("output").get("data"):
                            print(f"DEBUG: {service_name} process_completed successfully.")
                            return event_data["output"]["data"]
                        else:
                            error_msg = event_data.get("output", {}).get("error", f"Unknown error in {service_name} completion.")
                            error_msg_lower = error_msg.lower()
                            if any(kw in error_msg_lower for kw in ["cuda", "gpu", "quota", "capacity", "load", "queue full", "too many requests", "rate limit"]):
                                raise GPUQuotaError(f"Processing {service_name} failed with resource error: {error_msg}")
                            raise ValueError(f"Processing {service_name} failed: {error_msg}")
                    elif event_data.get("msg") == "process_generating":
                        # Simulate progress update to console or log
                        if time.time() - last_progress_update > 5: # Log every 5 seconds
                            print(f"DEBUG: {service_name} is generating...")
                            last_progress_update = time.time()
                    elif event_data.get("msg") == "queue_full":
                        raise GPUQuotaError(f"Queue for {service_name} is full.")
            
            time.sleep(2) # Poll every 2 seconds
        
        except requests.exceptions.Timeout:
            print(f"WARNING: Polling {service_name} timed out. Retrying...")
            time.sleep(5) # Wait longer on timeout
            continue
        except requests.exceptions.RequestException as e:
            error_detail = str(e)
            error_detail_lower = error_detail.lower()
            if any(kw in error_detail_lower for kw in ["cuda", "gpu", "quota", "capacity", "load", "queue full", "too many requests", "rate limit"]):
                raise GPUQuotaError(f"Polling {service_name} failed with resource error: {error_detail[:150]}")
            raise ConnectionError(f"Error during polling {service_name}: {error_detail[:100]}")
        except Exception as e:
            print(f"ERROR: Unhandled exception during polling {service_name}: {e}")
            time.sleep(5) # Wait on unexpected errors

    raise ConnectionError(f"Polling {service_name} stream timed out after {max_poll_time} seconds. No completion received.")


# --- Main image generation process with IPv6 rotation ---
async def start_full_process_with_ipv6_rotation(persian_prompt: str, aspect_ratio_key: str):
    global current_ipv6_index

    max_retries_per_ip = 2 # How many times to retry with the same IP before changing IP
    
    # Use a specific list of IPs for this request's attempts
    # This prevents concurrent requests from conflicting with `current_ipv6_index` directly
    # A more robust solution for high concurrency might involve a queue of available IPs
    available_ips_for_this_run = list(IPV6_ADDRESSES) # Copy the list
    if not available_ips_for_this_run:
        print("WARNING: No IPv6 addresses available. Attempting with default IP.")
        available_ips_for_this_run = [None] # Use default network behavior

    # Keep track of tried IPs to avoid immediate re-use in case of failure
    tried_ips_in_this_run = []

    while available_ips_for_this_run:
        # Get the next IP from the shuffled list
        # We pop to ensure we don't pick the same IP until all others are exhausted
        current_source_ip = available_ips_for_this_run.pop(0) 
        tried_ips_in_this_run.append(current_source_ip)

        print(f"INFO: Attempting with IPv6: {current_source_ip} (IPs remaining in pool: {len(available_ips_for_this_run)})")

        for retry_count in range(max_retries_per_ip):
            try:
                # 1. Translate Prompt
                print(f"INFO: Translating text... (Attempt {retry_count+1} with {current_source_ip})")
                translation_payload = [persian_prompt, *TRANSLATOR_OTHER_PARAMS]
                translator_session_hash = generate_session_hash()
                translator_event_id = await call_gradio_api_with_ipv6(
                    TRANSLATOR_API_BASE_URL,
                    TRANSLATOR_FN_INDEX,
                    translation_payload,
                    translator_session_hash,
                    source_ip=current_source_ip
                )
                translated_prompt_data = await poll_gradio_sse_status(
                    TRANSLATOR_API_BASE_URL,
                    translator_session_hash,
                    translator_event_id,
                    "Translator",
                    source_ip=current_source_ip
                )
                prompt_for_image = translated_prompt_data[0] if translated_prompt_data and translated_prompt_data[0] else ""
                if not prompt_for_image:
                    raise ValueError("Failed to get a valid translation.")
                print(f"INFO: Translated prompt: {prompt_for_image[:50]}...")

                # 2. Create Image
                print(f"INFO: Creating image... (Attempt {retry_count+1} with {current_source_ip})")
                dimensions = PREDEFINED_DIMENSIONS_MAP.get(aspect_ratio_key, PREDEFINED_DIMENSIONS_MAP["1:1"])
                image_gen_payload = [
                    prompt_for_image,
                    random.randint(0, 2147483647), # Random seed
                    DEFAULT_RANDOMIZE_SEED,
                    dimensions["width"],
                    dimensions["height"],
                    DEFAULT_GUIDANCE_SCALE,
                    DEFAULT_INFERENCE_STEPS
                ]
                image_gen_session_hash = generate_session_hash()
                image_gen_event_id = await call_gradio_api_with_ipv6(
                    IMAGE_GENERATOR_API_BASE_URL,
                    IMAGE_GENERATOR_FN_INDEX,
                    image_gen_payload,
                    image_gen_session_hash,
                    IMAGE_GENERATOR_TRIGGER_ID,
                    source_ip=current_source_ip
                )

                image_result_data = await poll_gradio_sse_status(
                    IMAGE_GENERATOR_API_BASE_URL,
                    image_gen_session_hash,
                    image_gen_event_id,
                    "ImageGenerator",
                    source_ip=current_source_ip
                )

                final_image_url = None
                if image_result_data and image_result_data[0] and isinstance(image_result_data[0], dict) and image_result_data[0].get("url"):
                    final_image_url = image_result_data[0]["url"]
                    if not final_image_url.startswith("http"):
                        final_image_url = f"{IMAGE_GENERATOR_API_BASE_URL}{final_image_url}"
                
                if final_image_url:
                    print(f"SUCCESS: Image created successfully: {final_image_url}")
                    return {"success": True, "imageUrl": final_image_url, "message": "تصویر با موفقیت ساخته شد."}
                else:
                    raise ValueError("Final image URL not received from Hugging Face.")

            except GPUQuotaError as e:
                print(f"GPU Quota/Resource Error with IP {current_source_ip}: {e.message}. Attempting next IP.")
                # Break inner retry loop to try a new IP
                break 
            except Exception as e:
                print(f"ERROR: General error with IP {current_source_ip} (Retry {retry_count+1}/{max_retries_per_ip}): {e}")
                if retry_count < max_retries_per_ip - 1:
                    await asyncio.sleep(2) # Short delay before retrying with the same IP
                else:
                    print(f"INFO: All retries failed for IP {current_source_ip}. Moving to next IP.")
                    # Break inner retry loop to try a new IP
                    break 

    print("ERROR: All IP rotation attempts failed. Persistent GPU quota or other issues.")
    return {"success": False, "error": "تمام تلاش‌ها برای تولید تصویر به دلیل محدودیت‌های سرور یا خطاهای پایدار شکست خوردند."}

# --- Frontend Serving and API Endpoints ---
class GenerateImageRequest(BaseModel):
    prompt: str
    aspectRatioKey: str

@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Serves the main HTML application."""
    # Ensure index.html is in the same directory as app.py (or modify path)
    return FileResponse("index.html")

@app.post("/generate-image")
async def generate_image_endpoint(request: GenerateImageRequest):
    """API endpoint to trigger image generation."""
    try:
        result = await start_full_process_with_ipv6_rotation(request.prompt, request.aspectRatioKey)
        if result["success"]:
            return JSONResponse(status_code=200, content=result)
        else:
            return JSONResponse(status_code=500, content={"message": result["error"]})
    except Exception as e:
        print(f"CRITICAL ERROR in /generate-image endpoint: {e}")
        return JSONResponse(status_code=500, content={"message": f"خطای داخلی سرور: {e}"})
