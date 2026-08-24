from google import genai
import os

api_key = os.getenv("API_KEY")


async def generate_with_gemini(prompt: str, model_name: str = "gemini-2.5-flash") -> str:
    """
    Initializes the Gemini client with an explicit API key and generates text.
    """
    # Initialize the client with the provided API key
    client = genai.Client(api_key=api_key)
    
    # Generate content using the specified model
    response = await client.models.generate_content(
        model=model_name,
        contents=prompt,
    )
    
    return response.text