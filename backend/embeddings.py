from functools import lru_cache
from threading import Lock


_embedding_lock = Lock()


@lru_cache(maxsize=1)
def get_embedding_model():
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    return SentenceTransformer(
        "all-MiniLM-L6-v2",
        model_kwargs={
            "torch_dtype": torch.float16,
            "low_cpu_mem_usage": True,
        },
    )


def create_embeddings(chunks):
    if not chunks:
        return []

    import torch

    with _embedding_lock:
        model = get_embedding_model()
        with torch.inference_mode():
            embeddings = model.encode(
                chunks,
                batch_size=16,
                convert_to_numpy=True,
                show_progress_bar=False,
            )

    return embeddings
