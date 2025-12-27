from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from uuid import UUID

class GeminiSettings(BaseModel):
    model: str = "gemini-2.0-flash-001"
    temperature: float = 0.2
    max_tokens: int = 500

class AnalyzeRequest(BaseModel):
    session_uuid: UUID
    message_uuid: UUID
    text: str
    settings: Optional[GeminiSettings] = None

class TrainCorrectionRequest(BaseModel):
    text: str
    correct_department_id: str
    language: str

class Candidate(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    score: float = 0.0

class RoutingResult(BaseModel):
    department_id: str
    intent: str
    confidence: int
    reason: str
