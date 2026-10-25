# Cosine similarity

Source: https://en.wikipedia.org/wiki/Cosine_similarity (Wikipedia, CC BY-SA 4.0)

In data analysis, cosine similarity is a measure of similarity between two non-zero vectors defined in an inner product space. Cosine similarity is the cosine of the angle between the vectors; that is, it is the dot product of the vectors divided by the product of their lengths. It follows that the cosine similarity does not depend on the magnitudes of the vectors, but only on their angle. The cosine similarity always belongs to the interval $[-1,+1].$
 For example, two proportional vectors have a cosine similarity of +1, two orthogonal vectors have a similarity of 0, and two opposite vectors have a similarity of −1. In some contexts, the component values of the vectors cannot be negative, in which case the cosine similarity is bounded in $[0,1]$ .
For example, in information retrieval and text mining, each word is assigned a different coordinate and a document is represented by the vector of the numbers of occurrences of each word in the document. Cosine similarity then gives a useful measure of how similar two documents are likely to be, in terms of their subject matter, and independently of the length of the documents.
The technique is also used to measure cohesion within clusters in the field of data mining.
One advantage of cosine similarity is its low complexity, especially for sparse vectors: only the non-zero coordinates need to be considered.
Other names for cosine similarity include Orchini similarity and Tucker coefficient of congruence; the Otsuka–Ochiai similarity (see below) is cosine similarity applied to binary data.

## Definition
The cosine of two non-zero vectors can be derived by using the Euclidean dot product formula:

$\mathbf {A} \cdot \mathbf {B} =\left\|\mathbf {A} \right\|\left\|\mathbf {B} \right\|\cos \theta $

Given two n-dimensional vectors of attributes, A and B, the cosine similarity, cos(θ), is represented using a dot product and magnitude as

          cosine similarity ${\text{cosine similarity}}=S_{C}(A,B):=\cos(\theta )={\mathbf {A} \cdot \mathbf {B}  \over \|\mathbf {A} \|\|\mathbf {B} \|}={\frac {\sum \limits _{i=1}^{n}{A_{i}B_{i}}}{{\sqrt {\sum \limits _{i=1}^{n}{A_{i}^{2}}}}\cdot {\sqrt {\sum \limits _{i=1}^{n}{B_{i}^{2}}}}}},$ where $A_{i}$ and $B_{i}$ are the $i$ th components of vectors $\mathbf {A} $ and $\mathbf {B} $ , respectively.
The resulting similarity ranges from −1 meaning exactly opposite, to +1 meaning exactly the same, with 0 indicating orthogonality (no correlation), while in-between values indicate intermediate similarity or dissimilarity.
For text matching, the attribute vectors A and B are usually the term frequency vectors of the documents. Cosine similarity can be seen as a method of normalizing document length during comparison. In the case of information retrieval, the cosine similarity of two documents will range from $0\to 1$ , since the term frequencies cannot be negative. This remains true when using TF-IDF weights. The angle between two term frequency vectors cannot be greater than 90°.
If the attribute vectors are normalized by subtracting the vector means (e.g., $A-{\bar {A}}$ ), the measure is called the centered cosine similarity and is equivalent to the Pearson correlation coefficient. For an example of centering, 

${\text{if}}\,A=[A_{1},A_{2}]^{T},{\text{ then }}{\bar {A}}=\left[{\frac {(A_{1}+A_{2})}{2}},{\frac {(A_{1}+A_{2})}{2}}\right]^{T},$

${\text{ so }}A-{\bar {A}}=\left[{\frac {(A_{1}-A_{2})}{2}},{\frac {(-A_{1}+A_{2})}{2}}\right]^{T}.$

### Cosine distance
When the distance between two unit-length vectors is defined to be the length of their vector difference then

$\operatorname {dist} (\mathbf {A} ,\mathbf {B} )={\sqrt {(\mathbf {A} -\mathbf {B} )\cdot (\mathbf {A} -\mathbf {B} )}}={\sqrt {\mathbf {A} \cdot \mathbf {A} -2(\mathbf {A} \cdot \mathbf {B} )+\mathbf {B} \cdot \mathbf {B} }}={\sqrt {2(1-S_{C}(\mathbf {A} ,\mathbf {B} ))}}\,.$

Nonetheless the cosine distance is often defined without the square root or factor of 2:

          cosine distance ${\text{cosine distance}}=D_{C}(A,B):=1-S_{C}(A,B)\,.$

By virtue of being proportional to squared Euclidean distance, the cosine distance is not a true distance metric; it does not exhibit the triangle inequality property — or, more formally, the Schwarz inequality — and it violates the coincidence axiom.  To repair the triangle inequality property while maintaining the same ordering, one can convert to Euclidean distance 
    {\textstyle {\sqrt {2(1-S_{C}(A,B))}}}
 or angular distance θ = arccos(SC(A, B)). Alternatively, the triangular inequality that does work for angular distances can be expressed directly in terms of the cosines; see below.

### Angular distance and similarity
The normalized angle, referred to as angular distance, between any two vectors $A$ and $B$ is a formal distance metric and can be calculated from the cosine similarity. The complement of the angular distance metric can then be used to define angular similarity function bounded between 0 and 1, inclusive.
When the vector elements may be positive or negative:

          angular distance
                cosine similarity ${\text{angular distance}}=D_{\theta }:={\frac {\arccos({\text{cosine similarity}})}{\pi }}={\frac {\theta }{\pi }}$ angular similarity
          angular distance ${\text{angular similarity}}=S_{\theta }:=1-{\text{angular distance}}=1-{\frac {\theta }{\pi }}$

Or, if the vector elements are always positive:

          angular distance
                cosine similarity ${\text{angular distance}}=D_{\theta }:={\frac {2\cdot \arccos({\text{cosine similarity}})}{\pi }}={\frac {2\theta }{\pi }}$ angular similarity
          angular distance ${\text{angular similarity}}=S_{\theta }:=1-{\text{angular distance}}=1-{\frac {2\theta }{\pi }}$

Unfortunately, computing the inverse cosine (arccos) function is slow, making the use of the angular distance more computationally expensive than using the more common (but not metric) cosine distance above.

### L2-normalized Euclidean distance
Another effective proxy for cosine distance can be obtained by $L_{2}$ normalisation of the vectors, followed by the application of normal Euclidean distance. Using this technique each term in each vector is first divided by the magnitude of the vector, yielding a vector of unit length. Then the Euclidean distance over the end-points of any two vectors is a proper metric which gives the same ordering as the cosine distance (a monotonic transformation of Euclidean distance; see below) for any comparison of vectors, and furthermore avoids the potentially expensive trigonometric operations required to yield a proper metric. Once the normalisation has occurred, the vector space can be used with the full range of techniques available to any Euclidean space, notably standard dimensionality reduction techniques. This normalised form distance is often used within many deep learning algorithms.

### Otsuka–Ochiai coefficient
In biology, there is a similar concept known as the Otsuka–Ochiai coefficient named after Yanosuke Otsuka (also spelled as Ōtsuka, Ootsuka or Otuka, Japanese: 大塚 弥之助) and Akira Ochiai (Japanese: 落合 明), also known as the Ochiai–Barkman or Ochiai coefficient, which can be represented as:

$K={\frac {|A\cap B|}{\sqrt {|A|\times |B|}}}$

Here, $A$ and $B$ are sets, and $|A|$ is the number of elements in $A$ . If sets are represented as bit vectors, the Otsuka–Ochiai coefficient can be seen to be the same as the cosine similarity. It is identical to the score introduced by Godfrey Thomson.
In a recent book, the coefficient is tentatively misattributed to another Japanese researcher with the family name Otsuka. The confusion arises because in 1957 Akira Ochiai attributes the coefficient only to Otsuka (no first name mentioned) by citing an article by Ikuso Hamai (Japanese: 浜井 生三), who in turn cites the original 1936 article by Yanosuke Otsuka.

## Properties
The most noteworthy property of cosine similarity is that it reflects a relative, rather than absolute, comparison of the individual vector dimensions. For any positive constant $a$ and vector $V$ , the vectors $V$ and $aV$ are maximally similar. The measure is thus most appropriate for data where frequency is more important than absolute values; notably, term frequency in documents.
However more recent metrics with a grounding in information theory, such as Jensen–Shannon, SED, and triangular divergence have been shown to have improved semantics in at least some contexts.

Cosine similarity is related to Euclidean distance as follows. Denote Euclidean distance by the usual $\|A-B\|$ , and observe that

$\|A-B\|^{2}=(A-B)\cdot (A-B)=\|A\|^{2}+\|B\|^{2}-2(A\cdot B)\ $
 (polarization identity)
by expansion. When A and B are normalized to unit length, $\|A\|^{2}=\|B\|^{2}=1$ so this expression is equal to

$2(1-\cos(A,B)).$

In short, the cosine distance can be expressed in terms of Euclidean distance as
