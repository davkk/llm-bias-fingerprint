#!/usr/bin/env bash

for model in "Qwen3.5-4B-Q3_K_M" "Qwen3.5-4B-Q4_0" "Qwen3.5-4B-Q5_K_M" "Qwen3.5-4B-Q6_K" "Qwen3.5-4B-Q8_0"; do
    curl "http://localhost:8000/runs" \
        -N -X POST \
        -H "Content-Type: application/json" \
        -d "{\"experiment\":\"color\",\"model\":\"$model\",\"prompts\":[\"random color\",\"give me a color\",\"what color is on your mind?\"],\"samples_per_prompt\":100}" \
    | jq
done
