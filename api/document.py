from fastapi import APIRouter, Request

from models.document import Document


router = APIRouter(
    prefix='/document', tags=['Document']
)

@router.get('/')
async def get_documents(
    request: Request
):
    db = request.app.mongodb["documents"]
    data = await db.find({}, {"_id": 0}).to_list(length=None)
    return {"message": "Get documents", "data": data}


@router.post('/')
async def create_document(
    request: Request,
    document: Document
):
    db = request.app.mongodb["documents"]
    document_data = document.model_dump() if hasattr(document, "model_dump") else document.dict()
    result = await db.insert_one(document_data)
    return {"message": "Document created", "id": str(result.inserted_id)}
