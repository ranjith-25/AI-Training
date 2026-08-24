import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.embeddings import embed_single
from pipeline.vector_store import FAISSVectorStore
from utils.prompts import RAG_SYSTEM_PROMPT, RAG_USER_TEMPLATE
from google import genai
from dotenv import load_dotenv
load_dotenv()

INDEX_DIR = "data/indexes"
questions = [
    "Does exclusion E-27 apply to a sudden mechanical breakdown of a water heater under HO-0308?",
    "Is a sudden mechanical failure of a water heater covered, or is it excluded as gradual wear and tear under HO-0308?",
    "Under HO-0308, if a water heater suddenly breaks down, does exclusion E-27 for wear and tear apply?"
]

client = genai.Client(api_key=os.getenv("API_KEY"))

for q in questions:
    print("======================================================================")
    print("Q:", q)
    print("======================================================================")
    qemb = embed_single(q)
    for s in ["naive_fixed", "structure_aware"]:
        store = FAISSVectorStore.load(os.path.join(INDEX_DIR, s))
        top = store.search(qemb, top_k=2)
        print("  Strategy:", s)
        for sc in top:
            print(f"    {sc.chunk_id} (section: {sc.metadata.get('section','n/a')})")
        
        # generate answer
        ctx_parts = []
        for sc in top:
            ctx_parts.append(
                f"[chunk_id={sc.chunk_id}] [form={sc.metadata.get('form_number','')}] "
                f"[section={sc.metadata.get('section','')}]\n{sc.text}"
            )
        context = "\n\n---\n\n".join(ctx_parts)
        prompt = f"{RAG_SYSTEM_PROMPT}\n\n{RAG_USER_TEMPLATE.format(context=context, question=q)}"
        resp = client.models.generate_content(model="gemini-flash-lite-latest", contents=prompt)
        print("  Answer:")
        print("    " + resp.text.replace("\n", "\n    "))
        print()
