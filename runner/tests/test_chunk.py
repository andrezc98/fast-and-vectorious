from fv.chunk import chunk_words, strip_html


def test_strip_html_unescapes_after_removing_tags():
    assert strip_html("<p>a &lt;b&gt;</p><code>x</code>") == "a <b> x"


def test_chunk_words_overlaps():
    text = " ".join(str(i) for i in range(500))
    parts = chunk_words(text, max_words=220, overlap=30)
    assert len(parts) == 3
    assert parts[1].split()[0] == "190"


def test_chunk_words_empty():
    assert chunk_words("   ") == []
