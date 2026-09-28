from orianbelt.rag.index import tokenize,chunk_text
def test_basic():
    assert "reserve" in tokenize("Reserve Bank of India")
    assert len(chunk_text(" ".join(["word"]*500),100,20))>1
