import sys
sys.path.append('src')
import 02_rag_ingestion as r
print(r.retrieve_context('roadmap', allowed_clearances=['standard']))