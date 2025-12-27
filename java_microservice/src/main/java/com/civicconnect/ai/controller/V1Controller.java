package com.civicconnect.ai.controller;

import com.civicconnect.ai.dto.AnalyzeRequest;
import com.civicconnect.ai.dto.TrainCorrectionRequest;
import com.civicconnect.ai.service.AiPipelineService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/v1")
@RequiredArgsConstructor
public class V1Controller {

    private final AiPipelineService aiPipelineService;

    @PostMapping("/analyze")
    public ResponseEntity<Map<String, Object>> analyzeMessage(@RequestBody AnalyzeRequest request) {
        // Trigger async processing
        aiPipelineService.processMessagePipeline(request);
        
        // Return immediately with acknowledgment
        return ResponseEntity.ok(Map.of(
            "status", "processing",
            "message_uuid", request.getMessageUuid().toString()
        ));
    }

    @PostMapping("/train-correction")
    public ResponseEntity<Map<String, String>> trainCorrection(@RequestBody TrainCorrectionRequest request) {
        try {
            aiPipelineService.trainCorrectionPipeline(request);
            return ResponseEntity.ok(Map.of("status", "success"));
        } catch (Exception e) {
            return ResponseEntity.internalServerError()
                .body(Map.of("status", "error", "detail", e.getMessage()));
        }
    }
}


