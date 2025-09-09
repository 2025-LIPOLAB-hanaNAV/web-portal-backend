# LIPOLAB FastAPI Backend

LIPOLAB 그룹포털 백엔드 API 서버

## 주요 기능

- 게시물 생성 (POST /api/posts)
- 게시물 조회 (GET /api/posts, GET /api/posts/{id})
- 첨부파일 업로드/다운로드
- Elasticsearch 기반 검색 (GET /api/search)
- Swagger API 문서 자동 생성

## 필요 조건

- Python 3.8+
- Elasticsearch 8.x (선택사항)

## 설치 및 실행

### 1. 가상환경 생성 및 활성화

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. 서버 실행

```bash
python main.py
```

또는

```bash
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

### 4. API 문서 확인

브라우저에서 다음 URL로 접속:
- Swagger UI: http://localhost:8002/docs
- ReDoc: http://localhost:8002/redoc

## API 엔드포인트

### 게시물 관련

- `POST /api/posts` - 게시물 생성 (첨부파일 및 이미지 업로드 가능)
- `GET /api/posts` - 모든 게시물 조회
- `GET /api/posts/{post_id}` - 특정 게시물 조회 (업로드된 이미지 정보 포함)
- `GET /api/search?q={query}` - 게시물 검색

### 이미지 업로드

- `POST /api/upload-image` - 게시물 내용에 삽입할 이미지 업로드
- `GET /static/images/{filename}` - 업로드된 이미지 조회

### 첨부파일

- `GET /api/attachments/{file_id}/download` - 첨부파일 다운로드

## POST 요청 예시 (Postman)

**URL:** `POST http://localhost:8002/api/posts`

**Body (form-data):**
```
title: 테스트 게시물
department: IT부서
author: 홍길동
category: 공지
content: <p>테스트 내용입니다. <img src="/static/images/abc123.jpg" alt="이미지"></p>
badges: ["notice", "important"]
endDate: 2025-12-31
files: (선택사항 - 첨부파일)
images: (선택사항 - 게시물 내용에 삽입할 이미지 파일들)
```

## 이미지 업로드 예시 (Postman)

**URL:** `POST http://localhost:8002/api/upload-image`

**Body (form-data):**
```
image: (이미지 파일 선택)
```

**응답 예시:**
```json
{
  "success": true,
  "imageId": "abc123456789",
  "imageUrl": "/static/images/abc123456789.jpg",
  "filename": "original_image.jpg"
}
```

## 게시물 데이터 구조

```json
{
  "id": "string",
  "title": "string",
  "department": "string", 
  "author": "string",
  "views": 0,
  "postDate": "2025-09-04",
  "endDate": "2025-12-31",
  "category": "string",
  "badges": ["notice", "emergency"],
  "content": "<p>HTML content</p>",
  "attachments": [
    {
      "id": "string",
      "name": "filename.pdf",
      "size": "780KB",
      "downloadUrl": "/api/attachments/id/download"
    }
  ],
  "uploaded_images": [
    {
      "id": "abc123456789",
      "filename": "image.jpg",
      "url": "/static/images/abc123456789.jpg"
    }
  ]
}
```

## Elasticsearch 설정 (선택사항)

Elasticsearch를 사용하려면:

1. Elasticsearch 8.x 설치
2. 기본 설정으로 실행 (http://localhost:9200)
3. 서버 시작 시 자동으로 인덱스 생성됨

Elasticsearch가 없어도 파일 기반 저장소로 정상 동작합니다.

## 포트 설정

- **백엔드 서버: 8002 포트**
- **프론트엔드 목업: 5173 포트** (기본 Vite 포트)

CORS가 설정되어 있어 프론트엔드에서 백엔드 API 호출 가능합니다.

## 데이터 저장

- **게시물:** `data/posts/` 디렉토리에 JSON 파일로 저장
- **첨부파일:** `data/uploads/` 디렉토리에 파일명으로 저장
- **이미지:** `data/images/` 디렉토리에 저장 (정적 파일로 제공)

## 트러블슈팅

### 포트 충돌
다른 포트를 사용하려면:
```bash
uvicorn main:app --host 0.0.0.0 --port 8003 --reload
```

### Method Not Allowed 오류
- POST 요청시 URL: `/api/posts`
- GET 요청시 URL: `/api/posts` 또는 `/api/posts/{id}`
- Content-Type: `multipart/form-data` (파일 업로드시)