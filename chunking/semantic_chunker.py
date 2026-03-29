# chunking/semantic_chunker.py

from typing import List, Dict


class SemanticChunker:
    """Chunk while preserving section context"""

    def __init__(self, chunk_size: int = 800, overlap: int = 100):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, blocks: List[Dict]) -> List[Dict]:
        """
        Chunk parsed blocks while keeping section metadata

        Returns:
        [
            {
                'text': 'combined text...',
                'page_numbers': [5, 6],
                'section': 'Technical Specifications',
                'subsection': '3.2 Glass Requirements',
                'start_page': 5,
                'end_page': 6
            },
            ...
        ]
        """

        chunks = []
        current_chunk_text = []
        current_chunk_tokens = 0
        current_metadata = {
            'pages': set(),
            'section': None,
            'subsection': None
        }

        for block in blocks:
            # Skip headings (they're captured in metadata)
            if block['is_heading']:
                continue

            block_text = block['text']
            block_tokens = len(block_text.split())  # Approximate

            # Update metadata
            current_metadata['pages'].add(block['page'])
            if block['section']:
                current_metadata['section'] = block['section']
            if block['subsection']:
                current_metadata['subsection'] = block['subsection']

            # Check if adding this block exceeds chunk size
            if current_chunk_tokens + block_tokens > self.chunk_size and current_chunk_text:
                # Finalize current chunk
                chunks.append(self._create_chunk(current_chunk_text, current_metadata))

                # Start new chunk with overlap
                overlap_text = ' '.join(current_chunk_text[-self.overlap:])
                current_chunk_text = [overlap_text, block_text]
                current_chunk_tokens = len(overlap_text.split()) + block_tokens
                current_metadata = {
                    'pages': {block['page']},
                    'section': block['section'],
                    'subsection': block['subsection']
                }
            else:
                current_chunk_text.append(block_text)
                current_chunk_tokens += block_tokens

        # Add final chunk
        if current_chunk_text:
            chunks.append(self._create_chunk(current_chunk_text, current_metadata))

        return chunks

    def _create_chunk(self, text_list: List[str], metadata: Dict) -> Dict:
        """Finalize chunk structure"""
        pages = sorted(list(metadata['pages']))

        return {
            'text': ' '.join(text_list),
            'page_numbers': pages,
            'start_page': pages[0] if pages else None,
            'end_page': pages[-1] if pages else None,
            'section': metadata['section'],
            'subsection': metadata['subsection']
        }