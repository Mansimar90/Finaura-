from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import os, uuid, json

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]
app = FastAPI(title='Finaura API')
api_router = APIRouter(prefix='/api')

MONTHS = ['Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug']
HISTORY = [
    {'month':'Mar','income':175000,'expenses':104000,'savings':71000,'savings_rate':41},
    {'month':'Apr','income':178000,'expenses':112000,'savings':66000,'savings_rate':37},
    {'month':'May','income':180000,'expenses':118000,'savings':62000,'savings_rate':34},
    {'month':'Jun','income':180000,'expenses':126000,'savings':54000,'savings_rate':30},
    {'month':'Jul','income':185000,'expenses':129000,'savings':56000,'savings_rate':30},
    {'month':'Aug','income':185000,'expenses':123000,'savings':62000,'savings_rate':34},
]
TRANSACTIONS = [
    {'id':'txn-1','date':'12 Aug 2026','description':'SWIGGY','amount':450,'type':'Expense','category':'Food'},
    {'id':'txn-2','date':'01 Aug 2026','description':'SALARY','amount':185000,'type':'Income','category':'Income'},
    {'id':'txn-3','date':'03 Aug 2026','description':'RENT PAYMENT','amount':32000,'type':'Expense','category':'Rent'},
    {'id':'txn-4','date':'06 Aug 2026','description':'AMAZON INDIA','amount':3890,'type':'Expense','category':'Shopping'},
    {'id':'txn-5','date':'08 Aug 2026','description':'METRO CARD','amount':1250,'type':'Expense','category':'Transport'},
    {'id':'txn-6','date':'10 Aug 2026','description':'NETFLIX','amount':649,'type':'Expense','category':'Entertainment'},
]
DEFAULT_GOALS = [
    {'id':'goal-1','name':'Higher Education','emoji':'🎓','target_amount':1000000,'current_amount':300000,'deadline':'2029','priority':'High','monthly_contribution':25000},
    {'id':'goal-2','name':'Emergency Fund','emoji':'◉','target_amount':300000,'current_amount':180000,'deadline':'2027','priority':'High','monthly_contribution':15000},
    {'id':'goal-3','name':'Car','emoji':'🚗','target_amount':800000,'current_amount':120000,'deadline':'2030','priority':'Medium','monthly_contribution':10000},
]

class GoalInput(BaseModel):
    name: str; target_amount: int; current_amount: int = 0; deadline: str; priority: str = 'Medium'; monthly_contribution: int = 0
class CategoryUpdate(BaseModel):
    category: str
class ChatInput(BaseModel):
    message: str

@api_router.get('/financial/overview')
async def overview():
    goals = await db.finaura_goals.find({}, {'_id':0}).to_list(20)
    if not goals: goals = DEFAULT_GOALS
    transactions = await db.finaura_transactions.find({}, {'_id':0}).to_list(100)
    if not transactions:
        await db.finaura_transactions.insert_many(TRANSACTIONS)
        transactions = [dict(t) for t in TRANSACTIONS]
    return {'user': {'name':'Aarav Sharma','occupation':'Product Designer','age':29}, 'summary': {'income':185000,'expenses':123000,'savings':62000,'current_savings':500000,'investments':250000,'debt':120000,'emi':18000,'net_worth':3485000,'health_score':78}, 'history':HISTORY, 'transactions':transactions, 'goals':goals, 'spending':[{'name':'Rent','value':32000,'color':'#0f172a'},{'name':'Food','value':18500,'color':'#10b981'},{'name':'Shopping','value':16000,'color':'#f59e0b'},{'name':'Transport','value':9200,'color':'#f97316'},{'name':'Other','value':47300,'color':'#cbd5e1'}]}

@api_router.post('/goals')
async def create_goal(goal: GoalInput):
    doc = goal.model_dump(); doc['id'] = str(uuid.uuid4()); await db.finaura_goals.insert_one(doc); return {key: value for key, value in doc.items() if key != '_id'}

@api_router.patch('/goals/{goal_id}')
async def update_goal(goal_id: str, goal: GoalInput):
    doc = goal.model_dump(); await db.finaura_goals.update_one({'id':goal_id},{'$set':doc}); return {'id':goal_id, **doc}

@api_router.patch('/transactions/{txn_id}')
async def update_transaction(txn_id: str, update: CategoryUpdate):
    await db.finaura_transactions.update_one({'id':txn_id},{'$set':{'category':update.category}}); return {'id':txn_id,'category':update.category}

@api_router.delete('/financial/data')
async def delete_data():
    await db.finaura_goals.delete_many({}); await db.finaura_transactions.delete_many({}); return {'deleted':True}

@api_router.post('/chat')
async def chat(payload: ChatInput):
    key = os.environ.get('EMERGENT_LLM_KEY')
    if not key: raise HTTPException(503, 'AI assistant is not configured')
    from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta
    system = 'You are Ask Finaura, a warm, concise financial education assistant. Use this fictional demo profile: Aarav Sharma, monthly income ₹185,000, expenses ₹123,000, savings ₹62,000, health score 78, goals Higher Education (high priority), Emergency Fund (high priority), Car (medium). Explain trends and concepts, never give personalized investment orders or claim bank access. Mention this is demo data when relevant.'
    async def stream():
        chat_client = LlmChat(api_key=key, session_id='finaura-demo-aarav', system_message=system).with_model('openai','gpt-5.4')
        async for event in chat_client.stream_message(UserMessage(text=payload.message)):
            if isinstance(event, TextDelta): yield event.content
    return StreamingResponse(stream(), media_type='text/plain', headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

app.include_router(api_router)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=os.environ.get('CORS_ORIGINS','*').split(','), allow_methods=['*'], allow_headers=['*'])
@app.on_event('shutdown')
async def shutdown_db_client(): client.close()