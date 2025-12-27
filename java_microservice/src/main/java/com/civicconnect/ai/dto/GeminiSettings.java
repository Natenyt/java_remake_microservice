package com.civicconnect.ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
public class GeminiSettings {
    private String model = "gemini-2.0-flash-001";
    private double temperature = 0.2;
    
    @JsonProperty("max_tokens")
    private int maxTokens = 500;
}


