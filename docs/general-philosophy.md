# <img src="img/ki.png" alt="ki logo" width="120" align="left" />  Knowledge Index (`ki`) Philosophy
<br clear="left">

`ki`'s mission is to be the safe, easy, fast search engine on your knowledge base.  Allowing agents and humans alike to quickly understand-\>search-\>retrieve in seconds without prior expertise. 

To accomplish this, `ki` establishes an opinionated scope:

First, core tenets. These won't change:
1. **Search, not storage:** While `ki` uses the [Neo4j database](https://neo4j.com/), `ki` itself is not a database solution, it is a search engine. `ki` simply syncs with the state of your filesystem.  `ki` is not designed to independently store data  
2. **Reconstructable state**: if you accidentally nuke your `ki` you can reconstruct it automatically to an identical state with one command: `ki index`. It is effectively disposable cache.
3. **Never alter source data:** Source data (files) are NEVER mutated by `ki`.

Consequences of core tenets and general philosophy. These are open to change If they don't violate the tenets:
1. **Reflect don’t enrich**: `ki` reflects the data on the file system as-is. It does not enrich it in any way.
2. **No LLMs at write time:** A consequence of the second core tenet. This makes `ki` lightweight. To be specific  
   1. No Tagging  
   2. No Entity Extraction  
   3. No Community summaries  
   4. No Similarity relationships, Entity Resolution, or Inferencing when indexing  
   5. No using LLMs in any way when constructing the underlying graph for the index

   If you are seeking these things…No harm in trying `ki` first and seeing if it works (it is safe, easy, fast, and free)  but consider advancing to using [Neo4j and its AI]([Neo4j database](https://neo4j.com/)) features directly. 

2. **No embeddings:** This makes `ki` AI-vendor neutral and instance to set up without having to worry about 3rd party integrations.  Read the blog (link TBD) to see how `ki` accomplishes semantic search without vectors.   
