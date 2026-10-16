import logging
from threading import Lock

import numpy as np

logger = logging.getLogger(__name__)

_embedding_lock = Lock()
_onnx_session = None
_tokenizer = None


def _get_onnx_resources():
    global _onnx_session, _tokenizer
    if _onnx_session is not None and _tokenizer is not None:
        return _onnx_session, _tokenizer

    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    logger.info("Initializing lightweight ONNX embedding engine for all-MiniLM-L6-v2...")
    model_path = hf_hub_download(
        repo_id="sentence-transformers/all-MiniLM-L6-v2",
        filename="onnx/model_O4.onnx",
    )
    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    session = ort.InferenceSession(
        model_path,
        sess_options=opts,
        providers=["CPUExecutionProvider"],
    )

    tokenizer = Tokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2")
    tokenizer.enable_padding(pad_token="[PAD]", pad_id=0)
    tokenizer.enable_truncation(max_length=256)

    _onnx_session = session
    _tokenizer = tokenizer
    logger.info("ONNX embedding engine initialized successfully.")
    return _onnx_session, _tokenizer


def create_embeddings(chunks):
    if not chunks:
        return []

    with _embedding_lock:
        try:
            session, tokenizer = _get_onnx_resources()
            batch_size = 32
            all_embeddings = []

            for i in range(0, len(chunks), batch_size):
                batch = [str(c) for c in chunks[i : i + batch_size]]
                encoded = tokenizer.encode_batch(batch)
                input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
                attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
                token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

                outputs = session.run(
                    None,
                    {
                        "input_ids": input_ids,
                        "attention_mask": attention_mask,
                        "token_type_ids": token_type_ids,
                    },
                )
                token_embeddings = outputs[0]

                # Mean pooling
                mask_expanded = np.expand_dims(attention_mask, -1).astype(np.float32)
                sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
                sum_mask = np.clip(mask_expanded.sum(axis=1), a_min=1e-9, a_max=None)
                mean_pooled = sum_embeddings / sum_mask

                # L2 normalize
                norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
                normalized = mean_pooled / np.clip(norms, a_min=1e-12, a_max=None)
                all_embeddings.append(normalized.astype(np.float32))

            return np.vstack(all_embeddings)
        except Exception:
            logger.warning("ONNX embedding failed; attempting fallback...", exc_info=True)
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)
            model = SentenceTransformer(
                "all-MiniLM-L6-v2",
                model_kwargs={
                    "torch_dtype": torch.float16,
                    "low_cpu_mem_usage": True,
                },
            )
            with torch.inference_mode():
                return model.encode(
                    chunks,
                    batch_size=16,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )

