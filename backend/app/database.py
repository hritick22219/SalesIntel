from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy.orm import declarative_base
from config import settings

# Declarative Base fallback if needed
Base = declarative_base()
engine = None

# Initialize Motor MongoDB Client
mongo_client = AsyncIOMotorClient(settings.mongo_connection_string)
mongo_db = mongo_client.get_database(settings.MONGODB_DB_NAME)

async def get_db():
    """FastAPI Dependency for obtaining MongoDB database reference."""
    yield mongo_db

def get_mongo_db():
    """Helper function to get direct AsyncIOMotorDatabase instance."""
    return mongo_db
