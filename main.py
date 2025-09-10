from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query
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
import base64
from html import unescape
import zipfile
import tempfile
import shutil

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

def strip_html_tags(html: str) -> str:
    if not html:
        return ""
    # remove script/style content
    html = re.sub(r"<\s*(script|style)[^>]*>[\s\S]*?<\s*/\s*\1\s*>", " ", html, flags=re.IGNORECASE)
    # remove tags
    text = re.sub(r"<[^>]+>", " ", html)
    # unescape entities
    text = unescape(text)
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text

def find_file_by_prefix(directory: str, prefix: str) -> Optional[str]:
    for filename in os.listdir(directory):
        if filename.startswith(prefix):
            return os.path.join(directory, filename)
    return None

def build_absolute_url(relative_url: str, base_url: Optional[str]) -> str:
    if not base_url:
        return relative_url
    return base_url.rstrip('/') + relative_url

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
    # 저장된 파일 찾기
    saved_filename = None
    for filename in os.listdir(UPLOADS_DIR):
        if filename.startswith(file_id):
            saved_filename = filename
            break
    
    if not saved_filename:
        raise HTTPException(status_code=404, detail="File not found")
    
    # 게시물에서 원본 파일명 찾기
    original_filename = saved_filename  # 기본값은 저장된 파일명
    posts = get_all_posts()
    
    for post in posts:
        if "attachments" in post:
            for attachment in post["attachments"]:
                if attachment["id"] == file_id:
                    # original_filename이 있으면 사용, 없으면 name 사용
                    original_filename = attachment.get("original_filename", attachment.get("name", saved_filename))
                    break
    
    file_path = os.path.join(UPLOADS_DIR, saved_filename)
    return FileResponse(file_path, filename=original_filename)

@app.get("/api/export/posts")
async def export_posts(
    format: str = Query("json", regex="^(json|zip)$", description="출력 형식: json (JSON 응답) 또는 zip (ZIP 파일 다운로드)"),
    include_files: str = Query("metadata", regex="^(none|metadata|files)$", description="첨부/이미지 포함 수준: none|metadata|files"),
    base_url: Optional[str] = Query(None, description="상대 경로 앞에 붙일 Base URL (예: https://example.com:8002)"),
):
    """
    모든 게시물과 연관 첨부파일 및 이미지를 내보내는 Export API
    - format=json: JSON 형태로 응답 (기존 동작)
    - format=zip: ZIP 파일로 다운로드 (posts.json + 모든 첨부파일/이미지 포함)
    """
    posts = get_all_posts()
    
    if format == "json":
        # 기존 JSON 응답 로직
        exported = []
        for post in posts:
            uploaded_images = post.get("uploaded_images") or []
            attachments = post.get("attachments") or []

            content_html = post.get("content", "")
            content_text = strip_html_tags(content_html)

            export_attachments = []
            for att in attachments:
                att_id = att.get("id")
                item = {
                    "id": att_id,
                    "name": att.get("name"),
                    "size_display": att.get("size"),
                    "download_url": build_absolute_url(att.get("downloadUrl", ""), base_url),
                }

                if include_files in ("metadata", "files"):
                    file_path = find_file_by_prefix(UPLOADS_DIR, att_id)
                    if file_path and os.path.exists(file_path):
                        mime, _ = mimetypes.guess_type(file_path)
                        try:
                            size_bytes = os.path.getsize(file_path)
                        except Exception:
                            size_bytes = None
                        item.update({
                            "path": file_path,
                            "mime_type": mime or "application/octet-stream",
                            "size_bytes": size_bytes,
                        })
                        if include_files == "files":
                            try:
                                with open(file_path, "rb") as f:
                                    b = f.read()
                                item["content_base64"] = base64.b64encode(b).decode("utf-8")
                                item["content_encoding"] = "base64"
                            except Exception:
                                pass
                    else:
                        item.update({"path": None})
                export_attachments.append(item)

            export_images = []
            for img in uploaded_images:
                img_id = img.get("id")
                rel_url = img.get("url", "")
                item = {
                    "id": img_id,
                    "filename": img.get("filename"),
                    "url": build_absolute_url(rel_url, base_url),
                }

                if include_files in ("metadata", "files"):
                    file_path = find_file_by_prefix(IMAGES_DIR, img_id)
                    if file_path and os.path.exists(file_path):
                        mime, _ = mimetypes.guess_type(file_path)
                        try:
                            size_bytes = os.path.getsize(file_path)
                        except Exception:
                            size_bytes = None
                        item.update({
                            "path": file_path,
                            "mime_type": mime or "application/octet-stream",
                            "size_bytes": size_bytes,
                        })
                        if include_files == "files":
                            try:
                                with open(file_path, "rb") as f:
                                    b = f.read()
                                item["content_base64"] = base64.b64encode(b).decode("utf-8")
                                item["content_encoding"] = "base64"
                            except Exception:
                                pass
                    else:
                        item.update({"path": None})
                export_images.append(item)

            exported.append({
                "id": post.get("id"),
                "title": post.get("title"),
                "content_html": content_html,
                "content_text": content_text,
                "metadata": {
                    "department": post.get("department"),
                    "author": post.get("author"),
                    "category": post.get("category"),
                    "badges": post.get("badges", []),
                    "postDate": post.get("postDate"),
                    "endDate": post.get("endDate"),
                    "views": post.get("views", 0),
                },
                "attachments": export_attachments,
                "images": export_images,
            })

        return {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "include_files": include_files,
            "count": len(exported),
            "posts": exported,
        }
    
    else:  # format == "zip"
        # ZIP 파일로 패키징하여 다운로드
        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = os.path.join(temp_dir, "posts_export")
            os.makedirs(export_dir)
            
            # posts.json 파일 생성
            posts_data = []
            for post in posts:
                content_html = post.get("content", "")
                content_text = strip_html_tags(content_html)
                
                post_export = {
                    "id": post.get("id"),
                    "title": post.get("title"),
                    "content_html": content_html,
                    "content_text": content_text,
                    "metadata": {
                        "department": post.get("department"),
                        "author": post.get("author"),
                        "category": post.get("category"),
                        "badges": post.get("badges", []),
                        "postDate": post.get("postDate"),
                        "endDate": post.get("endDate"),
                        "views": post.get("views", 0),
                    },
                    "attachments": [],
                    "images": []
                }
                
                # 첨부파일 처리
                if include_files == "files":
                    attachments_dir = os.path.join(export_dir, "attachments")
                    os.makedirs(attachments_dir, exist_ok=True)
                    
                    for att in post.get("attachments", []):
                        att_id = att.get("id")
                        original_name = att.get("original_filename") or att.get("name")
                        
                        file_path = find_file_by_prefix(UPLOADS_DIR, att_id)
                        if file_path and os.path.exists(file_path):
                            # 파일 복사 (원본명으로 저장)
                            safe_name = f"{att_id}_{original_name}"
                            dest_path = os.path.join(attachments_dir, safe_name)
                            shutil.copy2(file_path, dest_path)
                            
                            post_export["attachments"].append({
                                "id": att_id,
                                "original_name": original_name,
                                "file_path": f"attachments/{safe_name}",
                                "size_display": att.get("size")
                            })
                
                # 이미지 처리
                if include_files == "files":
                    images_dir = os.path.join(export_dir, "images")
                    os.makedirs(images_dir, exist_ok=True)
                    
                    for img in post.get("uploaded_images", []):
                        img_id = img.get("id")
                        original_name = img.get("original_filename") or img.get("filename")
                        
                        file_path = find_file_by_prefix(IMAGES_DIR, img_id)
                        if file_path and os.path.exists(file_path):
                            # 파일 복사 (원본명으로 저장)
                            safe_name = f"{img_id}_{original_name}"
                            dest_path = os.path.join(images_dir, safe_name)
                            shutil.copy2(file_path, dest_path)
                            
                            post_export["images"].append({
                                "id": img_id,
                                "original_name": original_name,
                                "file_path": f"images/{safe_name}",
                                "url": img.get("url")
                            })
                
                posts_data.append(post_export)
            
            # posts.json 저장
            posts_json_path = os.path.join(export_dir, "posts.json")
            with open(posts_json_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "exported_at": datetime.utcnow().isoformat() + "Z",
                    "include_files": include_files,
                    "count": len(posts_data),
                    "posts": posts_data
                }, f, ensure_ascii=False, indent=2)
            
            # ZIP 파일 생성
            zip_path = os.path.join(temp_dir, "posts_export.zip")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(export_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, export_dir)
                        zipf.write(file_path, arcname)
            
            # ZIP 파일 반환
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"posts_export_{timestamp}.zip"
            return FileResponse(zip_path, filename=filename, media_type="application/zip")

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
