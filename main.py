from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import json
import os
import uuid
from datetime import datetime
import aiofiles
from elasticsearch import Elasticsearch, AsyncElasticsearch

app = FastAPI(
    title="LIPOLAB Posts API",
    description="게시물 관리 API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

POSTS_DIR = "data/posts"
UPLOADS_DIR = "data/uploads"
ES_HOST = "http://localhost:9200"

os.makedirs(POSTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

es_client = None

class Attachment(BaseModel):
    id: str
    name: str
    size: str
    downloadUrl: str

class PostCreate(BaseModel):
    title: str
    department: str
    author: str
    category: str
    content: str
    endDate: Optional[str] = None
    badges: Optional[List[str]] = []

class PostResponse(BaseModel):
    id: str
    title: str
    department: str
    author: str
    views: int
    postDate: str
    endDate: Optional[str]
    category: str
    badges: List[str]
    content: str
    attachments: List[Attachment]

def get_es_client():
    global es_client
    if es_client is None:
        try:
            es_client = Elasticsearch([ES_HOST])
            if not es_client.ping():
                print("Elasticsearch connection failed")
                es_client = None
        except Exception as e:
            print(f"Elasticsearch error: {e}")
            es_client = None
    return es_client

@app.on_event("startup")
async def startup_event():
    es = get_es_client()
    if es:
        try:
            if not es.indices.exists(index="posts"):
                es.indices.create(
                    index="posts",
                    body={
                        "mappings": {
                            "properties": {
                                "title": {"type": "text", "analyzer": "standard"},
                                "content": {"type": "text", "analyzer": "standard"},
                                "department": {"type": "keyword"},
                                "author": {"type": "keyword"},
                                "category": {"type": "keyword"},
                                "postDate": {"type": "date"},
                                "endDate": {"type": "date"},
                                "badges": {"type": "keyword"},
                                "views": {"type": "integer"}
                            }
                        }
                    }
                )
        except Exception as e:
            print(f"Elasticsearch index creation error: {e}")

def save_post_to_file(post_id: str, post_data: dict):
    file_path = os.path.join(POSTS_DIR, f"{post_id}.json")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(post_data, f, ensure_ascii=False, indent=2)

def load_post_from_file(post_id: str) -> Optional[dict]:
    file_path = os.path.join(POSTS_DIR, f"{post_id}.json")
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def get_all_posts() -> List[dict]:
    posts = []
    for filename in os.listdir(POSTS_DIR):
        if filename.endswith('.json'):
            post_id = filename[:-5]
            post = load_post_from_file(post_id)
            if post:
                posts.append(post)
    return sorted(posts, key=lambda x: x.get('postDate', ''), reverse=True)

def index_post_to_es(post_data: dict):
    es = get_es_client()
    if es:
        try:
            es.index(index="posts", id=post_data["id"], body=post_data)
        except Exception as e:
            print(f"Elasticsearch indexing error: {e}")

@app.post("/api/posts", response_model=PostResponse)
async def create_post(
    title: str = Form(...),
    department: str = Form(...),
    author: str = Form(...),
    category: str = Form(...),
    content: str = Form(...),
    endDate: Optional[str] = Form(None),
    badges: Optional[str] = Form("[]"),
    files: Optional[List[UploadFile]] = File(None)
):
    post_id = str(uuid.uuid4()).replace('-', '')[:9]
    
    try:
        badges_list = json.loads(badges) if badges else []
    except:
        badges_list = []
    
    attachments = []
    if files:
        for file in files:
            if file.filename:
                file_id = str(uuid.uuid4())[:8]
                file_extension = os.path.splitext(file.filename)[1]
                saved_filename = f"{file_id}{file_extension}"
                file_path = os.path.join(UPLOADS_DIR, saved_filename)
                
                async with aiofiles.open(file_path, 'wb') as f:
                    file_content = await file.read()
                    await f.write(file_content)
                
                file_size = len(file_content)
                size_str = f"{file_size}B" if file_size < 1024 else f"{file_size//1024}KB"
                
                attachment = {
                    "id": file_id,
                    "name": file.filename,
                    "size": size_str,
                    "downloadUrl": f"/api/attachments/{file_id}/download"
                }
                attachments.append(attachment)
    
    post_data = {
        "id": post_id,
        "title": title,
        "department": department,
        "author": author,
        "views": 0,
        "postDate": datetime.now().strftime("%Y-%m-%d"),
        "endDate": endDate,
        "category": category,
        "badges": badges_list,
        "content": content,
        "attachments": attachments
    }
    
    save_post_to_file(post_id, post_data)
    index_post_to_es(post_data)
    
    return PostResponse(**post_data)

@app.get("/api/posts", response_model=List[PostResponse])
async def get_posts():
    posts = get_all_posts()
    return [PostResponse(**post) for post in posts]

@app.get("/api/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: str):
    post = load_post_from_file(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    post["views"] += 1
    save_post_to_file(post_id, post)
    
    return PostResponse(**post)

@app.get("/api/attachments/{file_id}/download")
async def download_attachment(file_id: str):
    for filename in os.listdir(UPLOADS_DIR):
        if filename.startswith(file_id):
            file_path = os.path.join(UPLOADS_DIR, filename)
            return FileResponse(file_path, filename=filename)
    
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/api/search")
async def search_posts(q: str):
    es = get_es_client()
    if not es:
        posts = get_all_posts()
        filtered_posts = [
            post for post in posts 
            if q.lower() in post.get("title", "").lower() or 
               q.lower() in post.get("content", "").lower()
        ]
        return {"posts": [PostResponse(**post) for post in filtered_posts]}
    
    try:
        query = {
            "query": {
                "multi_match": {
                    "query": q,
                    "fields": ["title^2", "content", "department", "author"]
                }
            }
        }
        
        result = es.search(index="posts", body=query)
        posts = [hit["_source"] for hit in result["hits"]["hits"]]
        return {"posts": [PostResponse(**post) for post in posts]}
    except Exception as e:
        print(f"Elasticsearch search error: {e}")
        return {"posts": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)