package com.civicconnect.ai.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class Candidate {
    private String id;
    private String name = "";
    private String description = "";
    private double score = 0.0;
}


