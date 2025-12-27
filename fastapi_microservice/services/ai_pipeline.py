import os
import time
import httpx
import uuid
import json
import asyncio
import logging
import sys
from typing import List, Dict, Any, Optional

import google.generativeai as genai
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

# Import models from the api/v1 folder
from api.v1.models import AnalyzeRequest, TrainCorrectionRequest, Candidate

# --- Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Docker Networking: Default to 'localhost' for local dev, override with 'qdrant' in Docker
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost") 
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
# Docker Networking: Default to 'http://127.0.0.1:8000' for local dev, override with 'http://django_backend:8000' in Docker
DJANGO_BACKEND_URL = os.getenv("DJANGO_BACKEND_URL", "http://127.0.0.1:8000")

# --- FORCE LOGGING TO CONSOLE ---
# This ensures logs appear in your terminal even if Uvicorn tries to capture them
logger = logging.getLogger("ai_pipeline")
logger.setLevel(logging.INFO)

# Check if handlers already exist to avoid double logging
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- Global Clients (Connection Pooling) ---
qdrant_client = None

def init_qdrant():
    """Initializes Qdrant Client with smart fallback to localhost."""
    global qdrant_client
    
    # Attempt 1: Configured Host (e.g., 'qdrant')
    try:
        logger.info(f"Attempting connection to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}...")
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        # Force a request to verify connectivity immediately
        client.get_collections() 
        logger.info(f"✅ SUCCESS: Connected to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")
        return client
    except Exception as e:
        logger.warning(f"⚠️ Failed to connect to {QDRANT_HOST}: {e}")
        
        # Attempt 2: Fallback to localhost if we aren't already there
        if QDRANT_HOST != "localhost" and QDRANT_HOST != "127.0.0.1":
            try:
                logger.info("🔄 Attempting fallback to 'localhost'...")
                client = QdrantClient(host="localhost", port=QDRANT_PORT)
                client.get_collections()
                logger.info(f"✅ SUCCESS: Connected to Qdrant at localhost:{QDRANT_PORT}")
                return client
            except Exception as e2:
                logger.error(f"❌ CRITICAL: Qdrant fallback failed: {e2}")
        else:
            logger.error(f"❌ CRITICAL: Qdrant connection impossible.")
            
    return None

# Initialize clients
qdrant_client = init_qdrant()
http_client = httpx.AsyncClient(timeout=10.0)

# --- Helper: Run Blocking Code in Threads ---
async def async_embed(text: str, model: str = "models/text-embedding-004"):
    """Runs the blocking Google Embed call in a separate thread"""
    return await asyncio.to_thread(
        genai.embed_content,
        model=model,
        content=text,
        task_type="retrieval_query"
    )

async def async_generate(model_name: str, prompt: str, config: dict):
    """Runs the blocking Google Generate call in a separate thread"""
    model = genai.GenerativeModel(model_name)
    return await asyncio.to_thread(
        model.generate_content,
        prompt,
        generation_config=config
    )

# --- Main Pipelines ---

async def process_message_pipeline(request: AnalyzeRequest):
    start_time = time.time()
    logger.info(f"\n--- START PIPELINE: {request.message_uuid} ---")
    logger.info(f"Input Text: {request.text[:100]}...") 

    processing_data = {
        "session_uuid": str(request.session_uuid),
        "message_uuid": str(request.message_uuid),
        "processing_time_ms": 0,
        "embedding_tokens": 0,
        "prompt_tokens": 0,
        "total_tokens": 0,
        "intent_label": "Unknown",
        "suggested_department_id": None,
        "confidence_score": 0,
        "reason": "Processing initialized."
    }
    
    text = request.text
    
    # Step 1: Language Detection
    language = "uz"
    if any("\u0400" <= char <= "\u04FF" for char in text): # Cyrillic check
        language = "ru"
    
    processing_data["language_detected"] = language
    logger.info(f"Step 1 [Lang Detect]: Detected '{language}'")

    # Step 2: Injection Detection
    is_injection = False
    risk_score = 0.0
    injection_keywords = ["ignore previous instructions", "system prompt", "delete all data"]
    
    if any(keyword in text.lower() for keyword in injection_keywords):
        is_injection = True
        risk_score = 0.95
    
    logger.info(f"Step 2 [Injection]: Is Injection? {is_injection} (Risk: {risk_score})")

    if is_injection:
        processing_data["risk_score"] = risk_score
        processing_data["reason"] = "Potential injection keywords detected"
        processing_data["processing_time_ms"] = int((time.time() - start_time) * 1000)
        
        logger.warning(f"Injection detected! Aborting and sending alert.")
        await send_webhook(f"{DJANGO_BACKEND_URL}/api/internal/injection-alert/", processing_data)
        return

    # Step 3: Vector Embedding (Non-Blocking)
    embedding_model = "models/text-embedding-004"
    vector = []
    try:
        logger.info(f"Step 3 [Embedding]: Requesting embedding from Gemini ({embedding_model})...")
        embedding_result = await async_embed(text, embedding_model)
        vector = embedding_result['embedding']
        processing_data["embedding_tokens"] = len(text) // 4 
        logger.info(f"Step 3 [Embedding]: Success. Vector length: {len(vector)}")
    except Exception as e:
        logger.error(f"Step 3 [Embedding] FAILED: {e}")
        # We fail gracefully here, stopping pipeline
        return 
        
    # Step 4: Semantic Search
    candidates = []
    if qdrant_client:
        try:
            logger.info(f"Step 4 [Search]: Querying Qdrant (Collection: 'departments')...")
            
            # First check if collection exists and has points
            try:
                collection_info = await asyncio.to_thread(
                    qdrant_client.get_collection,
                    collection_name="departments"
                )
                
                if collection_info.points_count == 0:
                    logger.warning(f"Step 4 [Search]: Collection 'departments' is empty. Please index departments first.")
                    # Skip the query if collection is empty
                else:
                    logger.info(f"Step 4 [Search]: Collection has {collection_info.points_count} points.")
                    
                    # Debug: Try to check what language values exist in the collection
                    # First try without filter to see sample payloads
                    try:
                        sample_response = await asyncio.to_thread(
                            qdrant_client.scroll,
                            collection_name="departments",
                            limit=5,
                            with_payload=True,
                            with_vectors=False
                        )
                        sample_points = sample_response[0]  # Returns (points, next_page_offset)
                        if sample_points:
                            sample_languages = [p.payload.get("language") for p in sample_points if hasattr(p, 'payload')]
                            logger.info(f"Step 4 [Search]: Sample language values in collection: {set(sample_languages)}")
                    except Exception as debug_error:
                        logger.warning(f"Step 4 [Search]: Could not get sample data for debugging: {debug_error}")
                    
                    q_filter = Filter(
                        must=[
                            FieldCondition(
                                key="language",
                                match=MatchValue(value=language)
                            )
                        ]
                    )
                    logger.info(f"Step 4 [Search]: Filter: language == '{language}'")

                    # --- QDRANT V1.16+ FIX ---
                    # The "OutputTooSmall" error occurs when filter returns fewer results than limit
                    # Try with progressively smaller limits, then fallback to no filter
                    search_response = None
                    hits = []
                    
                    # Try filtered query with decreasing limits
                    for limit in [3, 2, 1]:
                        try:
                            logger.info(f"Step 4 [Search]: Attempting query with limit={limit} and language filter...")
                            search_response = await asyncio.to_thread(
                                qdrant_client.query_points,
                                collection_name="departments",
                                query=vector,
                                query_filter=q_filter,
                                limit=limit,
                                with_payload=True,
                                with_vectors=False
                            )
                            hits = search_response.points if hasattr(search_response, 'points') else []
                            if hits:
                                logger.info(f"Step 4 [Search]: Query succeeded with limit={limit}, found {len(hits)} hits.")
                                break
                        except Exception as query_error:
                            error_str = str(query_error)
                            if "OutputTooSmall" in error_str or "500" in error_str:
                                logger.warning(f"Step 4 [Search]: Query failed with limit={limit}: {error_str[:100]}")
                                if limit > 1:
                                    continue  # Try with smaller limit
                                else:
                                    # Last attempt failed, try without filter as fallback
                                    logger.warning("Step 4 [Search]: Filtered query failed, trying without language filter...")
                                    try:
                                        search_response = await asyncio.to_thread(
                                            qdrant_client.query_points,
                                            collection_name="departments",
                                            query=vector,
                                            limit=5,  # Get more results when no filter
                                            with_payload=True,
                                            with_vectors=False
                                        )
                                        all_hits = search_response.points if hasattr(search_response, 'points') else []
                                        # Filter results by language in Python instead
                                        filtered_hits = [h for h in all_hits if h.payload.get("language") == language]
                                        if filtered_hits:
                                            hits = filtered_hits[:3]  # Take top 3 matching language
                                            logger.info(f"Step 4 [Search]: Query without filter succeeded, filtered to {len(hits)} {language} results.")
                                        else:
                                            # No matching language, use all results
                                            hits = all_hits[:3]
                                            logger.warning(f"Step 4 [Search]: No {language} results found, using top 3 results regardless of language.")
                                        break
                                    except Exception as fallback_error:
                                        logger.error(f"Step 4 [Search]: Fallback query also failed: {fallback_error}")
                                        raise query_error
                            else:
                                raise
                    
                    logger.info(f"Step 4 [Search]: Found {len(hits)} hits.")
                    
                    for i, hit in enumerate(hits):
                        payload = hit.payload
                        dept_id = payload.get("department_id")
                        name = payload.get("name")
                        score = hit.score if hasattr(hit, 'score') else 0.0
                        logger.info(f"   Hit #{i+1}: Score={score:.4f}, ID={dept_id}, Name={name}")
                        
                        candidates.append(Candidate(
                            id=str(dept_id),
                            name=name,
                            description=payload.get("description", ""),
                            score=score
                        ))
            except Exception as coll_error:
                # Collection might not exist or be inaccessible
                logger.error(f"Step 4 [Search]: Failed to access collection: {coll_error}")
                # Check if collection exists
                try:
                    collections = await asyncio.to_thread(qdrant_client.get_collections)
                    collection_names = [c.name for c in collections.collections]
                    if "departments" not in collection_names:
                        logger.warning("Step 4 [Search]: Collection 'departments' does not exist. Please run 'python manage.py index_departments' first.")
                    else:
                        logger.error(f"Step 4 [Search]: Collection exists but query failed: {coll_error}")
                except:
                    pass
                    
        except Exception as e:
             logger.error(f"Step 4 [Search] FAILED: {e}")
             # Log full error details for debugging
             import traceback
             logger.error(f"Full traceback: {traceback.format_exc()}")
    else:
        logger.error("Step 4 [Search] SKIPPED: Qdrant client is not connected.")

    # --- CRITICAL SAFETY CHECK ---
    # If Qdrant returns 0 results (empty database), DO NOT ask Gemini to pick an ID.
    if not candidates:
        logger.warning("Step 5 [LLM]: SKIPPED. No candidates found in Qdrant (Database might be empty).")
        processing_data["reason"] = "No relevant department found in knowledge base."
        # We allow the process to continue to Step 6, but with NO suggested ID.
    else:
        # Step 5: LLM Reranking & Decision
        model_name = request.settings.model if request.settings else "gemini-2.0-flash-001"
        temperature = request.settings.temperature if request.settings else 0.2
        
        candidates_str = "\n".join([f"ID: {c.id}, Name: {c.name}, Desc: {c.description}" for c in candidates])
        
        prompt = f"""
        You are a government classification AI. Analyze the user message and route it to the best department.
        
        User Message: "{text}"
        Language: {language}
        
        Available Departments (Top Matches):
        {candidates_str}
        
        Instructions:
        1. Classify the Intent (Shikoyat, Talab, Taklif).   
        2. Select the single best Department ID from the list provided above.
        3. CRITICAL: You must return an ID that exists in the list. Do not invent IDs.
        4. Provide a Confidence Score (0-100) and a short Reason.
        
        Output JSON format:
        {{
            "department_id": "UUID",
            "intent": "String",
            "confidence": Integer,
            "reason": "String"
        }}
        """
        
        logger.info(f"Step 5 [LLM]: Sending prompt to {model_name}...")

        try:
            response = await async_generate(
                model_name, 
                prompt, 
                {"temperature": temperature, "response_mime_type": "application/json"}
            )
            
            result_text = response.text
            logger.info(f"Step 5 [LLM]: Raw Response: {result_text}")

            if result_text.startswith("```json"):
                result_text = result_text[7:-3]
                
            llm_result = json.loads(result_text)
            
            processing_data["intent_label"] = llm_result.get("intent")
            processing_data["suggested_department_id"] = llm_result.get("department_id")
            processing_data["confidence_score"] = llm_result.get("confidence")
            # Ideally fetch name from candidate list matching ID
            processing_data["suggested_department_name"] = "Unknown" 
            processing_data["reason"] = llm_result.get("reason")
            
            usage = response.usage_metadata
            if usage:
                processing_data["prompt_tokens"] = usage.prompt_token_count
                processing_data["total_tokens"] = usage.total_token_count
            
            logger.info(f"Step 5 [LLM]: Parsed successfully. Suggested Dept: {processing_data['suggested_department_id']}")

        except Exception as e:
            error_str = str(e)
            # Check if it's a quota/rate limit error
            if "429" in error_str or "quota" in error_str.lower() or "rate limit" in error_str.lower():
                logger.warning(f"Step 5 [LLM]: Quota/Rate limit exceeded. Using top vector search result as fallback.")
                
                # Fallback: Use the top candidate from vector search
                if candidates:
                    top_candidate = candidates[0]
                    processing_data["intent_label"] = "Auto-detected"
                    processing_data["suggested_department_id"] = top_candidate.id
                    processing_data["confidence_score"] = int(top_candidate.score * 100)  # Convert 0-1 to 0-100
                    processing_data["suggested_department_name"] = top_candidate.name
                    processing_data["reason"] = f"LLM unavailable (quota exceeded). Using top vector search result with {top_candidate.score:.2%} similarity."
                    logger.info(f"Step 5 [LLM]: Fallback applied. Using Dept ID={top_candidate.id}, Name={top_candidate.name}, Score={top_candidate.score:.2%}")
                else:
                    processing_data["reason"] = "LLM quota exceeded and no vector search results available."
            else:
                logger.error(f"Step 5 [LLM] FAILED: {e}")
                processing_data["reason"] = f"LLM Error: {e}"

    # Step 6: Completion
    processing_data["processing_time_ms"] = int((time.time() - start_time) * 1000)
    processing_data["vector_search_results"] = [c.dict() for c in candidates]
    
    logger.info(f"Step 6 [Completion]: Sending webhook to Django ({DJANGO_BACKEND_URL})...")
    await send_webhook(f"{DJANGO_BACKEND_URL}/api/internal/routing-result/", processing_data)
    logger.info(f"--- END PIPELINE: {request.message_uuid} ---")

async def send_webhook(url: str, data: Dict[str, Any]):
    """Uses global http_client for better performance"""
    try:
        # Debug print payload keys to ensure we aren't sending massive binary blobs
        logger.info(f"Sending webhook to {url} | Keys: {list(data.keys())}")
        response = await http_client.post(url, json=data)
        logger.info(f"Webhook response status: {response.status_code}")
        
        if response.status_code != 200:
             logger.error(f"Django Error Body: {response.text}")
             
    except Exception as e:
        logger.error(f"Webhook connection failed: {e}")

async def train_correction_pipeline(request: TrainCorrectionRequest):
    logger.info(f"--- START TRAINING: {request.text[:50]}... ---")
    
    embedding_result = await async_embed(request.text)
    vector = embedding_result['embedding']
    logger.info(f"Generated embedding vector.")
    
    NAMESPACE = uuid.UUID('d87b3c2a-9e5f-4b1d-8c6a-2f3e4d5c6b7a')
    point_id = str(uuid.uuid5(NAMESPACE, f"{request.text}_{request.language}"))
    logger.info(f"Generated Point ID: {point_id}")
    
    if qdrant_client:
        await asyncio.to_thread(
            qdrant_client.upsert,
            collection_name="departments",
            points=[{
                "id": point_id,
                "vector": vector,
                "payload": {
                    "department_id": request.correct_department_id,
                    "language": request.language,
                    "name": "User Correction",
                    "description": request.text,
                    "is_correction": True
                }
            }]
        )
        logger.info(f"Upserted correction to Qdrant: {point_id}")
    else:
        logger.error("Qdrant client not connected, skipping upsert.")