# CivicConnect AI Microservice (Java)

A Spring Boot service that automatically routes citizen messages to the correct government department.

## What It Does

When a citizen sends a message (complaint, request, or suggestion), we need to figure out which department should handle it. Instead of making humans sort through thousands of messages, this service does it automatically:

1. **Vector Search** — We convert the message into an embedding and search Qdrant for the 3 most similar departments based on past messages
2. **LLM Selection** — Gemini looks at those 3 candidates and picks the best match, also classifying the intent (complaint/request/suggestion)
3. **Self-Improving** — When an operator corrects a wrong routing, we train the system by adding that example to the search database, so it learns from mistakes

This two-stage approach (vector search → LLM) gives us speed (Qdrant is fast) and accuracy (Gemini makes the final call).

## Requirements

- Java 11+
- Maven 3.6+
- Qdrant running on port 6334
- Gemini API key

## Quick Start

```bash
# Set your API key
set GEMINI_API_KEY=your_key_here

# Run the service
mvn spring-boot:run
```

Server starts at `http://localhost:8001`

## API Endpoints

### POST /api/v1/analyze
Analyze a message and route it to the appropriate department.

```json
{
  "session_uuid": "uuid-here",
  "message_uuid": "uuid-here", 
  "text": "Your message text"
}
```

Returns immediately with processing status. Results are sent via webhook to Django backend.

### POST /api/v1/train-correction
When an operator notices the AI routed a message to the wrong department, they correct it. This endpoint takes that correction and adds it to Qdrant — next time a similar message comes in, the vector search will find this example and route it correctly.

```json
{
  "text": "Original message",
  "correct_department_id": "uuid-of-correct-dept",
  "language": "uz"
}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | - | Google Gemini API key |
| `QDRANT_HOST` | localhost | Qdrant server host |
| `QDRANT_PORT` | 6334 | Qdrant gRPC port |
| `DJANGO_BACKEND_URL` | http://127.0.0.1:8000 | Django webhook URL |

## How It Works

1. Detects language (Uzbek/Russian)
2. Checks for prompt injection attempts
3. Generates text embedding via Gemini
4. Searches Qdrant for similar departments
5. Uses Gemini LLM to pick best match
6. Sends result to Django via webhook

