from fastapi import APIRouter, BackgroundTasks, HTTPException
from api.v1.models import AnalyzeRequest, TrainCorrectionRequest
from services.ai_pipeline import process_message_pipeline, train_correction_pipeline

router = APIRouter()

@router.post("/analyze")
async def analyze_message(request: AnalyzeRequest, background_tasks: BackgroundTasks):
    # Acknowledge receipt
    # We pass the processing to background tasks
    background_tasks.add_task(process_message_pipeline, request)
    return {"status": "processing", "message_uuid": request.message_uuid}

@router.post("/train-correction")
async def train_correction(request: TrainCorrectionRequest):
    try:
        await train_correction_pipeline(request)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
