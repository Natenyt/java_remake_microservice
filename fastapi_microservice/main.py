import uvicorn
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from api.v1.routes import router as v1_router

app = FastAPI(title="CivicConnect AI Microservice")

app.include_router(v1_router, prefix="/api/v1")

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=True)
