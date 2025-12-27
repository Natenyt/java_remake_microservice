package com.civicconnect.ai.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
public class TrainCorrectionRequest {
    private String text;
    
    @JsonProperty("correct_department_id")
    private String correctDepartmentId;
    
    private String language;
}


