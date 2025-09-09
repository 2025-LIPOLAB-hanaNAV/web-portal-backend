from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import json
import os
import uuid
from datetime import datetime
import aiofiles
import mimetypes
import re
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
IMAGES_DIR = "data/images"
ES_HOST = "http://localhost:9200"

os.makedirs(POSTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

app.mount("/static/images", StaticFiles(directory=IMAGES_DIR), name="images")

es_client = None

def clean_filename(filename: str) -> str:
    """
    한글 및 특수문자가 포함된 파일명을 영문으로 정리
    """
    if not filename:
        return "image"
    
    # 파일명과 확장자 분리
    name, ext = os.path.splitext(filename)
    
    # 한글 및 특수문자 제거, 영문과 숫자만 유지
    clean_name = re.sub(r'[^a-zA-Z0-9._-]', '', name)
    
    # 빈 문자열이면 기본값 사용
    if not clean_name:
        clean_name = "image"
    
    # 최대 길이 제한
    if len(clean_name) > 50:
        clean_name = clean_name[:50]
    
    return clean_name + ext

class Attachment(BaseModel):
    id: str
    name: str
    size: str
    downloadUrl: str
    original_filename: Optional[str] = None

class PostCreate(BaseModel):
    title: str
    department: str
    author: str
    category: str
    content: str
    endDate: Optional[str] = None
    badges: Optional[List[str]] = []

class UploadedImage(BaseModel):
    id: str
    filename: str
    url: str
    original_filename: Optional[str] = None

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
    uploaded_images: Optional[List[UploadedImage]] = []

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
    title: str = Form(..., description="게시물 제목"),
    department: str = Form(..., description="부서명"),
    author: str = Form(..., description="작성자"),
    category: str = Form(..., description="카테고리"),
    content: str = Form(..., description="게시물 내용 (HTML 형식, 이미지 태그 포함 가능)"),
    endDate: Optional[str] = Form(None, description="종료일 (YYYY-MM-DD 형식)"),
    badges: Optional[str] = Form("[]", description="뱃지 목록 (JSON 배열 형식)"),
    files: List[UploadFile] = File([], description="첨부파일 목록 (단일 또는 다중 파일)"),
    images: List[UploadFile] = File([], description="게시물 내용에 삽입할 이미지 목록 (단일 또는 다중 파일)")
):
    post_id = str(uuid.uuid4()).replace('-', '')[:9]
    
    try:
        badges_list = json.loads(badges) if badges else []
    except:
        badges_list = []
    
    attachments = []
    if files and len(files) > 0:
        for file in files:
            # 빈 파일이 아닌지 확인
            if file.filename and file.filename.strip():
                try:
                    file_id = str(uuid.uuid4())[:8]
                    # 원본 파일명 정리 (한글 처리)
                    clean_original_name = clean_filename(file.filename)
                    file_extension = os.path.splitext(clean_original_name)[1] or ""
                    saved_filename = f"{file_id}{file_extension}"
                    file_path = os.path.join(UPLOADS_DIR, saved_filename)
                    
                    async with aiofiles.open(file_path, 'wb') as f:
                        file_content = await file.read()
                        await f.write(file_content)
                    
                    file_size = len(file_content)
                    if file_size < 1024:
                        size_str = f"{file_size}B"
                    elif file_size < 1024 * 1024:
                        size_str = f"{file_size//1024}KB"
                    else:
                        size_str = f"{file_size//(1024*1024)}MB"
                    
                    attachment = {
                        "id": file_id,
                        "name": clean_original_name,
                        "size": size_str,
                        "downloadUrl": f"/api/attachments/{file_id}/download",
                        "original_filename": file.filename
                    }
                    attachments.append(attachment)
                except Exception as e:
                    print(f"Error processing attachment {file.filename}: {e}")
    
    uploaded_images = []
    image_tags = []
    
    if images and len(images) > 0:
        for image in images:
            # 빈 파일이 아니고 이미지 파일인지 확인
            if (image.filename and image.filename.strip() and 
                hasattr(image, 'content_type') and image.content_type):
                
                if image.content_type.startswith("image/"):
                    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp", "image/bmp"]
                    if image.content_type in allowed_types:
                        try:
                            image_id = str(uuid.uuid4())[:12]
                            
                            # 원본 파일명 정리 (한글 제거)
                            clean_original_name = clean_filename(image.filename)
                            file_extension = os.path.splitext(clean_original_name)[1] or ".jpg"
                            
                            # 저장될 파일명 생성 (고유 ID + 확장자)
                            saved_filename = f"{image_id}{file_extension}"
                            file_path = os.path.join(IMAGES_DIR, saved_filename)
                            
                            async with aiofiles.open(file_path, 'wb') as f:
                                image_content = await image.read()
                                await f.write(image_content)
                            
                            image_url = f"/static/images/{saved_filename}"
                            uploaded_images.append({
                                "id": image_id,
                                "filename": clean_original_name,
                                "url": image_url,
                                "original_filename": image.filename  # 원본 파일명도 보존
                            })
                            
                            # alt 속성에는 정리된 파일명 사용
                            image_tag = f'<img src="{image_url}" alt="{clean_original_name}" style="max-width: 100%; height: auto;">'
                            image_tags.append(image_tag)
                        except Exception as e:
                            print(f"Error processing image {image.filename}: {e}")
                    else:
                        print(f"Unsupported image type: {image.content_type} for file {image.filename}")
                else:
                    print(f"File is not an image: {image.content_type} for file {image.filename}")
    
    if image_tags:
        images_html = "<div>" + "".join(image_tags) + "</div>"
        content = content + images_html
    
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
        "attachments": attachments,
        "uploaded_images": uploaded_images
    }
    
    save_post_to_file(post_id, post_data)
    index_post_to_es(post_data)
    
    response_data = {**post_data}
    if uploaded_images:
        response_data["message"] = f"Post created successfully with {len(uploaded_images)} image(s) uploaded"
    
    return PostResponse(**response_data)

@app.get("/api/posts", response_model=List[PostResponse])
async def get_posts():
    posts = get_all_posts()
    for post in posts:
        if "uploaded_images" not in post:
            post["uploaded_images"] = []
    return [PostResponse(**post) for post in posts]

@app.get("/api/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: str):
    post = load_post_from_file(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    if "uploaded_images" not in post:
        post["uploaded_images"] = []
    
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

@app.post("/api/upload-image")
async def upload_image(
    image: UploadFile = File(...)
):
    """
    게시물 내용에 삽입할 이미지 업로드
    """
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")
    
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp"]
    if image.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Unsupported image format")
    
    image_id = str(uuid.uuid4())[:12]
    file_extension = os.path.splitext(image.filename)[1] if image.filename else ".jpg"
    saved_filename = f"{image_id}{file_extension}"
    file_path = os.path.join(IMAGES_DIR, saved_filename)
    
    try:
        async with aiofiles.open(file_path, 'wb') as f:
            content = await image.read()
            await f.write(content)
        
        image_url = f"/static/images/{saved_filename}"
        
        return {
            "success": True,
            "imageId": image_id,
            "imageUrl": image_url,
            "filename": image.filename or saved_filename
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload image: {str(e)}")

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