import pandas as pd, numpy as np
df = pd.read_parquet("embedded.parquet")
print(df.shape, df.columns.tolist())
v = np.frombuffer(df.embedding.iloc[0], dtype=np.float32)
print("dim:", v.shape, "norm:", np.linalg.norm(v).round(4))   # expect 1024, 1.0
print(df.status.value_counts())