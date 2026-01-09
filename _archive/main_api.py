# main_api.py (or wherever you create the FastAPI app)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.scrape_api import router as scrape_router

app = FastAPI()

# CORS — adjust as needed for your UI origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scrape_router)