from motor.motor_asyncio import AsyncIOMotorClient
import dns.resolver
from config import MONGO_URI

# Configure dnspython to use Google DNS for robust MongoDB SRV resolution
dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
dns.resolver.default_resolver.nameservers = ['8.8.8.8']

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["opptrax_db"]

tasks_collection = db["active_tasks"]
intent_log_collection = db["intent_logs"]
users_collection = db["users"]
opportunities_collection = db["opportunities"]
tracked_collection = db["tracked_board"]
