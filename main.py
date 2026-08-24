from fastapi import FastAPI
import os
from dotenv import load_dotenv
from fastapi.concurrency import asynccontextmanager
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi.middleware.cors import CORSMiddleware

from api.document import router as document_router

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Perform startup tasks here
    app.mongodb_client = AsyncIOMotorClient(f"mongodb+srv://{os.getenv('MONGODB_USERNAME')}:{os.getenv('MONGODB_PASSWORD')}@{os.getenv('MONGODB_HOSTNAME')}")
    app.mongodb = app.mongodb_client[os.getenv("MONGODB_DATABASE")]
    print("Starting up...")
    yield
    # Perform shutdown tasks here
    app.mongodb_client.close()
    print("Shutting down...")

app = FastAPI(
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86400,
)

@app.get("/")
def read_root():
    return {"Hello": "World"}


app.include_router(document_router)