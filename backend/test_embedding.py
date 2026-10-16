from embeddings import create_embeddings

text = "What is DBMS?"
embedding = create_embeddings([text])[0]

print("Number of values:", len(embedding))
print("First 10 values:", embedding[:10])