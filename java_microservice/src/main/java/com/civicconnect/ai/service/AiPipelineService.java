package com.civicconnect.ai.service;

import com.civicconnect.ai.dto.AnalyzeRequest;
import com.civicconnect.ai.dto.Candidate;
import com.civicconnect.ai.dto.TrainCorrectionRequest;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.qdrant.client.QdrantClient;
import io.qdrant.client.QdrantGrpcClient;
import io.qdrant.client.grpc.Points.*;
import io.qdrant.client.grpc.JsonWithInt;
import javax.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.util.*;
import java.util.concurrent.ExecutionException;

import static io.qdrant.client.PointIdFactory.id;
import static io.qdrant.client.ValueFactory.value;
import static io.qdrant.client.VectorsFactory.vectors;
import static io.qdrant.client.WithPayloadSelectorFactory.enable;

@Slf4j
@Service
public class AiPipelineService {

    @Value("${gemini.api.key:}")
    private String geminiApiKey;

    @Value("${qdrant.host:localhost}")
    private String qdrantHost;

    @Value("${qdrant.port:6334}")
    private int qdrantPort;

    @Value("${django.backend.url:http://127.0.0.1:8000}")
    private String djangoBackendUrl;

    private final WebClient webClient;
    private final ObjectMapper objectMapper;
    private QdrantClient qdrantClient;

    private static final List<String> INJECTION_KEYWORDS = List.of(
        "ignore previous instructions", "system prompt", "delete all data"
    );

    public AiPipelineService(WebClient.Builder webClientBuilder, ObjectMapper objectMapper) {
        this.webClient = webClientBuilder.build();
        this.objectMapper = objectMapper;
    }

    @PostConstruct
    public void init() {
        try {
            qdrantClient = new QdrantClient(
                QdrantGrpcClient.newBuilder(qdrantHost, qdrantPort, false).build()
            );
            qdrantClient.listCollectionsAsync().get();
            log.info("✅ Connected to Qdrant at {}:{}", qdrantHost, qdrantPort);
        } catch (Exception e) {
            log.warn("⚠️ Failed to connect to Qdrant: {}", e.getMessage());
            qdrantClient = null;
        }
    }

    @Async
    public void processMessagePipeline(AnalyzeRequest request) {
        long startTime = System.currentTimeMillis();
        log.info("\n--- START PIPELINE: {} ---", request.getMessageUuid());
        log.info("Input Text: {}...", request.getText().substring(0, Math.min(100, request.getText().length())));

        Map<String, Object> processingData = new HashMap<>();
        processingData.put("session_uuid", request.getSessionUuid().toString());
        processingData.put("message_uuid", request.getMessageUuid().toString());
        processingData.put("processing_time_ms", 0);
        processingData.put("intent_label", "Unknown");
        processingData.put("suggested_department_id", null);
        processingData.put("confidence_score", 0);
        processingData.put("reason", "Processing initialized.");

        String text = request.getText();

        // Step 1: Language Detection
        String language = detectLanguage(text);
        processingData.put("language_detected", language);
        log.info("Step 1 [Lang Detect]: Detected '{}'", language);

        // Step 2: Injection Detection
        boolean isInjection = INJECTION_KEYWORDS.stream()
            .anyMatch(kw -> text.toLowerCase().contains(kw));
        log.info("Step 2 [Injection]: Is Injection? {}", isInjection);

        if (isInjection) {
            processingData.put("risk_score", 0.95);
            processingData.put("reason", "Potential injection keywords detected");
            processingData.put("processing_time_ms", System.currentTimeMillis() - startTime);
            log.warn("Injection detected! Aborting and sending alert.");
            sendWebhook(djangoBackendUrl + "/api/internal/injection-alert/", processingData);
            return;
        }

        // Step 3: Vector Embedding
        List<Float> vector;
        try {
            log.info("Step 3 [Embedding]: Requesting embedding from Gemini...");
            vector = getEmbedding(text);
            log.info("Step 3 [Embedding]: Success. Vector length: {}", vector.size());
        } catch (Exception e) {
            log.error("Step 3 [Embedding] FAILED: {}", e.getMessage());
            return;
        }

        // Step 4: Semantic Search
        List<Candidate> candidates = new ArrayList<>();
        if (qdrantClient != null) {
            try {
                log.info("Step 4 [Search]: Querying Qdrant (Collection: 'departments')...");
                candidates = searchDepartments(vector, language);
                log.info("Step 4 [Search]: Found {} hits.", candidates.size());
            } catch (Exception e) {
                log.error("Step 4 [Search] FAILED: {}", e.getMessage());
            }
        } else {
            log.error("Step 4 [Search] SKIPPED: Qdrant client is not connected.");
        }

        // Step 5: LLM Reranking & Decision
        if (candidates.isEmpty()) {
            log.warn("Step 5 [LLM]: SKIPPED. No candidates found in Qdrant.");
            processingData.put("reason", "No relevant department found in knowledge base.");
        } else {
            try {
                String modelName = request.getSettings() != null ? 
                    request.getSettings().getModel() : "gemini-2.0-flash-001";
                double temperature = request.getSettings() != null ? 
                    request.getSettings().getTemperature() : 0.2;

                log.info("Step 5 [LLM]: Sending prompt to {}...", modelName);
                Map<String, Object> llmResult = rerankWithLlm(text, language, candidates, modelName, temperature);
                
                processingData.put("intent_label", llmResult.get("intent"));
                processingData.put("suggested_department_id", llmResult.get("department_id"));
                processingData.put("confidence_score", llmResult.get("confidence"));
                processingData.put("reason", llmResult.get("reason"));
                
                log.info("Step 5 [LLM]: Parsed successfully. Suggested Dept: {}", llmResult.get("department_id"));
            } catch (Exception e) {
                log.error("Step 5 [LLM] FAILED: {}", e.getMessage());
                // Fallback to top candidate
                if (!candidates.isEmpty()) {
                    Candidate top = candidates.get(0);
                    processingData.put("intent_label", "Auto-detected");
                    processingData.put("suggested_department_id", top.getId());
                    processingData.put("confidence_score", (int)(top.getScore() * 100));
                    processingData.put("reason", "LLM unavailable. Using top vector search result.");
                }
            }
        }

        // Step 6: Completion
        processingData.put("processing_time_ms", System.currentTimeMillis() - startTime);
        processingData.put("vector_search_results", candidates);

        log.info("Step 6 [Completion]: Sending webhook to Django...");
        sendWebhook(djangoBackendUrl + "/api/internal/routing-result/", processingData);
        log.info("--- END PIPELINE: {} ---", request.getMessageUuid());
    }

    public void trainCorrectionPipeline(TrainCorrectionRequest request) {
        log.info("--- START TRAINING: {}... ---", request.getText().substring(0, Math.min(50, request.getText().length())));

        try {
            List<Float> vector = getEmbedding(request.getText());
            log.info("Generated embedding vector.");

            String pointId = UUID.nameUUIDFromBytes(
                (request.getText() + "_" + request.getLanguage()).getBytes()
            ).toString();
            log.info("Generated Point ID: {}", pointId);

            if (qdrantClient != null) {
                qdrantClient.upsertAsync(
                    "departments",
                    List.of(
                        PointStruct.newBuilder()
                            .setId(id(UUID.fromString(pointId)))
                            .setVectors(vectors(vector))
                            .putAllPayload(Map.of(
                                "department_id", value(request.getCorrectDepartmentId()),
                                "language", value(request.getLanguage()),
                                "name", value("User Correction"),
                                "description", value(request.getText()),
                                "is_correction", value(true)
                            ))
                            .build()
                    )
                ).get();
                log.info("Upserted correction to Qdrant: {}", pointId);
            }
        } catch (Exception e) {
            log.error("Training failed: {}", e.getMessage());
            throw new RuntimeException("Training failed", e);
        }
    }

    private String detectLanguage(String text) {
        for (char c : text.toCharArray()) {
            if (c >= '\u0400' && c <= '\u04FF') {
                return "ru";
            }
        }
        return "uz";
    }

    private List<Float> getEmbedding(String text) {
        String url = "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key=" + geminiApiKey;

        Map<String, Object> body = Map.of(
            "model", "models/text-embedding-004",
            "content", Map.of("parts", List.of(Map.of("text", text))),
            "taskType", "RETRIEVAL_QUERY"
        );

        JsonNode response = webClient.post()
            .uri(url)
            .bodyValue(body)
            .retrieve()
            .bodyToMono(JsonNode.class)
            .block();

        List<Float> vector = new ArrayList<>();
        if (response != null && response.has("embedding")) {
            response.get("embedding").get("values").forEach(v -> vector.add((float) v.asDouble()));
        }
        return vector;
    }

    private List<Candidate> searchDepartments(List<Float> vector, String language) 
            throws ExecutionException, InterruptedException {
        
        List<Candidate> candidates = new ArrayList<>();

        List<ScoredPoint> results = qdrantClient.searchAsync(
            SearchPoints.newBuilder()
                .setCollectionName("departments")
                .addAllVector(vector)
                .setLimit(3)
                .setWithPayload(enable(true))
                .setFilter(Filter.newBuilder()
                    .addMust(Condition.newBuilder()
                        .setField(FieldCondition.newBuilder()
                            .setKey("language")
                            .setMatch(Match.newBuilder().setKeyword(language).build())
                            .build())
                        .build())
                    .build())
                .build()
        ).get();

        for (ScoredPoint hit : results) {
            Map<String, JsonWithInt.Value> payload = hit.getPayloadMap();
            candidates.add(new Candidate(
                payload.get("department_id").getStringValue(),
                payload.get("name").getStringValue(),
                payload.containsKey("description") ? payload.get("description").getStringValue() : "",
                hit.getScore()
            ));
        }

        return candidates;
    }

    private Map<String, Object> rerankWithLlm(String text, String language, 
            List<Candidate> candidates, String modelName, double temperature) {
        
        StringBuilder candidatesStr = new StringBuilder();
        for (Candidate c : candidates) {
            candidatesStr.append(String.format("ID: %s, Name: %s, Desc: %s\n", 
                c.getId(), c.getName(), c.getDescription()));
        }

        String prompt = String.format(
            "You are a government classification AI. Analyze the user message and route it to the best department.\n\n" +
            "User Message: \"%s\"\n" +
            "Language: %s\n\n" +
            "Available Departments (Top Matches):\n%s\n\n" +
            "Instructions:\n" +
            "1. Classify the Intent (Shikoyat, Talab, Taklif).\n" +
            "2. Select the single best Department ID from the list provided above.\n" +
            "3. CRITICAL: You must return an ID that exists in the list. Do not invent IDs.\n" +
            "4. Provide a Confidence Score (0-100) and a short Reason.\n\n" +
            "Output JSON format:\n" +
            "{\"department_id\": \"UUID\", \"intent\": \"String\", \"confidence\": Integer, \"reason\": \"String\"}",
            text, language, candidatesStr);

        String url = "https://generativelanguage.googleapis.com/v1beta/models/" + modelName + 
            ":generateContent?key=" + geminiApiKey;

        Map<String, Object> body = Map.of(
            "contents", List.of(Map.of("parts", List.of(Map.of("text", prompt)))),
            "generationConfig", Map.of(
                "temperature", temperature,
                "responseMimeType", "application/json"
            )
        );

        JsonNode response = webClient.post()
            .uri(url)
            .bodyValue(body)
            .retrieve()
            .bodyToMono(JsonNode.class)
            .block();

        try {
            String resultText = response.get("candidates").get(0)
                .get("content").get("parts").get(0).get("text").asText();
            return objectMapper.readValue(resultText, Map.class);
        } catch (Exception e) {
            throw new RuntimeException("Failed to parse LLM response", e);
        }
    }

    private void sendWebhook(String url, Map<String, Object> data) {
        try {
            log.info("Sending webhook to {} | Keys: {}", url, data.keySet());
            webClient.post()
                .uri(url)
                .bodyValue(data)
                .retrieve()
                .bodyToMono(String.class)
                .doOnSuccess(resp -> log.info("Webhook response received"))
                .doOnError(err -> log.error("Webhook failed: {}", err.getMessage()))
                .subscribe();
        } catch (Exception e) {
            log.error("Webhook connection failed: {}", e.getMessage());
        }
    }
}

