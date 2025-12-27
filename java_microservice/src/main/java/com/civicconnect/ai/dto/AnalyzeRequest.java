package com.civicconnect.ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import java.util.UUID;

@Data
public class AnalyzeRequest {
    @JsonProperty("session_uuid")
    private UUID sessionUuid;
    
    @JsonProperty("message_uuid")
    private UUID messageUuid;
    
    private String text;
    private GeminiSettings settings;
}


