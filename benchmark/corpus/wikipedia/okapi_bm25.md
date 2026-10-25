# Okapi BM25

Source: https://en.wikipedia.org/wiki/Okapi_BM25 (Wikipedia, CC BY-SA 4.0)

In information retrieval, Okapi BM25 (BM is an abbreviation of best matching) is a ranking function used by search engines to estimate the relevance of documents to a given search query. It is based on the probabilistic retrieval framework developed in the 1970s and 1980s by Stephen E. Robertson, Karen Spärck Jones, and others.
The name of the actual ranking function is BM25. The fuller name, Okapi BM25, includes the name of the first system to use it, which was the Okapi information retrieval system, implemented at London's City University in the 1980s and 1990s. BM25 and its newer variants, e.g. BM25F (a version of BM25 that can take document structure and anchor text into account), represent TF-IDF-like retrieval functions used in document retrieval.

## The ranking function
BM25 is a bag-of-words retrieval function that ranks a set of documents based on the query terms appearing in each document, regardless of their proximity within the document. It is a family of scoring functions with slightly different components and parameters. One of the most prominent instantiations of the function is as follows.
Given a query Q, containing keywords $q_{1},...,q_{n}$ , the BM25 score of a document D is:

${\text{score}}(D,Q)=\sum _{i=1}^{n}{\text{IDF}}(q_{i})\cdot {\frac {f(q_{i},D)\cdot (k_{1}+1)}{f(q_{i},D)+k_{1}\cdot \left(1-b+b\cdot {\frac {|D|}{\text{avgdl}}}\right)}}$ where $f(q_{i},D)$ is the number of times that the keyword $q_{i}$ occurs in the document D, $|D|$ is the length of the document D in words, and avgdl is the average document length in the text collection from which documents are drawn. $k_{1}$ and b are free parameters, usually chosen, in absence of an advanced optimization, as $k_{1}\in [1.2,2.0]$ and $b=0.75$ . ${\text{IDF}}(q_{i})$ is the IDF (inverse document frequency) weight of the query term $q_{i}$ . It is usually computed as:

${\text{IDF}}(q_{i})=\ln \left({\frac {N-n(q_{i})+0.5}{n(q_{i})+0.5}}+1\right)$ where  N is the total number of documents in the collection, and $n(q_{i})$ is the number of documents containing $q_{i}$ .
There are several interpretations for IDF and slight variations on its formula. In the original BM25 derivation, the IDF component is derived from the Binary Independence Model.

## IDF information theoretic interpretation
Here is an interpretation from information theory. Suppose a query term $q$ appears in $n(q)$ documents. Then a randomly picked document $D$ will contain the term with probability ${\frac {n(q)}{N}}$
 (where $N$ is again the cardinality of the set of documents in the collection). Therefore, the information content of the message " $D$ contains $q$
" is:

$-\log {\frac {n(q)}{N}}=\log {\frac {N}{n(q)}}.$

Now suppose we have two query terms $q_{1}$ and $q_{2}$ . If the two terms occur in documents entirely independently of each other, then the probability of seeing both $q_{1}$ and $q_{2}$ in a randomly picked document $D$ is:

${\frac {n(q_{1})}{N}}\cdot {\frac {n(q_{2})}{N}},$ and the information content of such an event is:

$\sum _{i=1}^{2}\log {\frac {N}{n(q_{i})}}.$

With a small variation, this is exactly what is expressed by the IDF component of BM25.

## Modifications
At the extreme values of the coefficient b BM25 turns into ranking functions known as BM11 (for $b=1$ ) and BM15 (for $b=0$ ).
BM25F (or the BM25 model with Extension to Multiple Weighted Fields) is a modification of BM25 in which the document is considered to be composed from several fields (such as headlines, main text, anchor text) with possibly different degrees of importance, term relevance saturation and length normalization. BM25F defines each type of field as a stream, applying a per-stream weighting to scale each stream against the calculated score.
BM25+ is an extension of BM25. BM25+ was developed to address one deficiency of the standard BM25 in which the component of term frequency normalization by document length is not properly lower-bounded; as a result of this deficiency, long documents which do match the query term can often be scored unfairly by BM25 as having a similar relevancy to shorter documents that do not contain the query term at all. The scoring formula of BM25+ only has one additional free parameter $\delta $
 (the default value is 1.0) as compared with BM25:

${\text{score}}(D,Q)=\sum _{i=1}^{n}{\text{IDF}}(q_{i})\cdot \left[{\frac {f(q_{i},D)\cdot (k_{1}+1)}{f(q_{i},D)+k_{1}\cdot \left(1-b+b\cdot {\frac {|D|}{\text{avgdl}}}\right)}}+\delta \right]$
