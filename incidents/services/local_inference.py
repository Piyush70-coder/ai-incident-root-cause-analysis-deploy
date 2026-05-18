import json
import os

from django.conf import settings


class LocalPhi3Fallback:
    _model = None
    _tokenizer = None
    _device = "cpu"

    @classmethod
    def get_model(cls):
        if cls._model is None:
            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer

            hf_token = getattr(settings, "HF_TOKEN", os.environ.get("HF_TOKEN"))
            cls._device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if cls._device == "cuda" else torch.float32

            model_path = "unsloth/Phi-3-mini-4k-instruct"
            adapter_path = os.path.join(
                settings.BASE_DIR,
                "incidents",
                "ai_models",
                "custom_model",
            )

            base_model = AutoModelForCausalLM.from_pretrained(
                model_path,
                torch_dtype=dtype,
                token=hf_token or None,
                low_cpu_mem_usage=True,
            )
            if cls._device == "cuda":
                base_model = base_model.to("cuda")

            cls._model = PeftModel.from_pretrained(base_model, adapter_path)
            cls._tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                token=hf_token or None,
            )

        return cls._model, cls._tokenizer

    @classmethod
    def analyze(cls, log_text):
        try:
            import torch

            model, tokenizer = cls.get_model()
            prompt = (
                "Analyze the following system logs and provide a complete, "
                "production-grade incident analysis. Return ONLY valid JSON.\n\n"
                f"Log: {log_text}"
            )

            inputs = tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=3072,
            )
            inputs = {key: value.to(cls._device) for key, value in inputs.items()}
            with torch.no_grad():
                outputs = model.generate(**inputs, max_new_tokens=512)
            response = tokenizer.decode(outputs[0], skip_special_tokens=True)

            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            return json.loads(response[json_start:json_end])
        except Exception as exc:
            print(f"Local fallback failed: {exc}")
            return None
