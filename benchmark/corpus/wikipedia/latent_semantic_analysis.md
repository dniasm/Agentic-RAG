# Latent semantic analysis

Source: https://en.wikipedia.org/wiki/Latent_semantic_analysis (Wikipedia, CC BY-SA 4.0)

Latent semantic analysis (LSA) is a technique in natural language processing, in particular distributional semantics, of analyzing relationships between a set of documents and the terms they contain by producing a set of concepts related to the documents and terms.  LSA assumes that words that are close in meaning will occur in similar pieces of text (the distributional hypothesis).  A matrix containing word counts per document (rows represent unique words and columns represent each document) is constructed from a large piece of text and a mathematical technique called singular value decomposition (SVD) is used to reduce the number of rows while preserving the similarity structure among columns.  Documents are then compared by cosine similarity between any two columns.  Values close to 1 represent very similar documents while values close to 0 represent very dissimilar documents.
An information retrieval technique using latent semantic structure was patented in 1988 by Scott Deerwester, Susan Dumais, George Furnas, Richard Harshman, Thomas Landauer, Karen Lochbaum and Lynn Streeter. In the context of its application to information retrieval, it is sometimes called latent semantic indexing (LSI).

## Overview

### Occurrence matrix
LSA can use a document-term matrix which describes the occurrences of terms in documents; it is a sparse matrix whose rows correspond to terms and whose columns correspond to documents. A typical example of the weighting of the elements of the matrix is tf-idf (term frequency–inverse document frequency): the weight of an element of the matrix is proportional to the number of times the terms appear in each document, where rare terms are upweighted to reflect their relative importance.
This matrix is also common to standard semantic models, though it is not necessarily explicitly expressed as a matrix, since the mathematical properties of matrices are not always used.

### Rank lowering
After the construction of the occurrence matrix, LSA finds a low-rank approximation to the term-document matrix. There could be various reasons for these approximations:

The original term-document matrix is presumed too large for the computing resources; in this case, the approximated low rank  matrix is interpreted as an approximation (a "least and necessary evil").
The original term-document matrix is presumed noisy: for example, anecdotal instances of terms are to be eliminated. From this point of view, the approximated matrix is interpreted as a de-noisified matrix (a better matrix than the original).
The original term-document matrix is presumed overly sparse relative to the "true" term-document matrix.  That is, the original matrix lists only the words actually in each document, whereas we might be interested in all words related to each document—generally a much larger set due to synonymy.
The consequence of the rank lowering is that some dimensions are combined and depend on more than one term:

{(car), (truck), (flower)} → {(1.3452 * car + 0.2828 * truck), (flower)}
This mitigates the problem of identifying synonymy, as the rank lowering is expected to merge the dimensions associated with terms that have similar meanings. It also partially mitigates the problem with polysemy, since components of polysemous words that point in the "right" direction are added to the components of words that share a similar meaning. Conversely, components that point in other directions tend to either simply cancel out, or, at worst, to be smaller than components in the directions corresponding to the intended sense.

### Derivation
Let $X$ be a matrix where element $(i,j)$ describes the occurrence of term $i$ in document $j$
 (this can be, for example, the frequency). $X$ will look like this:

${\begin{matrix}&{\textbf {d}}_{j}\\&\downarrow \\{\textbf {t}}_{i}^{T}\rightarrow &{\begin{bmatrix}x_{1,1}&\dots &x_{1,j}&\dots &x_{1,n}\\\vdots &\ddots &\vdots &\ddots &\vdots \\x_{i,1}&\dots &x_{i,j}&\dots &x_{i,n}\\\vdots &\ddots &\vdots &\ddots &\vdots \\x_{m,1}&\dots &x_{m,j}&\dots &x_{m,n}\\\end{bmatrix}}\end{matrix}}$

Now a row in this matrix will be a vector corresponding to a term, giving its relation to each document:

${\textbf {t}}_{i}^{T}={\begin{bmatrix}x_{i,1}&\dots &x_{i,j}&\dots &x_{i,n}\end{bmatrix}}$

Likewise, a column in this matrix will be a vector corresponding to a document, giving its relation to each term:

${\textbf {d}}_{j}={\begin{bmatrix}x_{1,j}\\\vdots \\x_{i,j}\\\vdots \\x_{m,j}\\\end{bmatrix}}$

Now the dot product ${\textbf {t}}_{i}^{T}{\textbf {t}}_{p}$ between two term vectors gives the correlation between the terms over the set of documents. The matrix product $XX^{T}$ contains all these dot products. Element $(i,p)$
 (which is equal to element $(p,i)$ ) contains the dot product ${\textbf {t}}_{i}^{T}{\textbf {t}}_{p}$
 ( $={\textbf {t}}_{p}^{T}{\textbf {t}}_{i}$ ). Likewise, the matrix $X^{T}X$ contains the dot products between all the document vectors, giving their correlation over the terms: ${\textbf {d}}_{j}^{T}{\textbf {d}}_{q}={\textbf {d}}_{q}^{T}{\textbf {d}}_{j}$ .
Now, from the theory of linear algebra, there exists a decomposition of $X$ such that $U$ and $V$ are orthogonal matrices and $\Sigma $ is a diagonal matrix. This is called a singular value decomposition (SVD):

${\begin{matrix}X=U\Sigma V^{T}\end{matrix}}$

The matrix products giving us the term and document correlations then become

${\begin{matrix}XX^{T}&=&(U\Sigma V^{T})(U\Sigma V^{T})^{T}=(U\Sigma V^{T})(V^{T^{T}}\Sigma ^{T}U^{T})=U\Sigma V^{T}V\Sigma ^{T}U^{T}=U\Sigma \Sigma ^{T}U^{T}\\X^{T}X&=&(U\Sigma V^{T})^{T}(U\Sigma V^{T})=(V^{T^{T}}\Sigma ^{T}U^{T})(U\Sigma V^{T})=V\Sigma ^{T}U^{T}U\Sigma V^{T}=V\Sigma ^{T}\Sigma V^{T}\end{matrix}}$

Since $\Sigma \Sigma ^{T}$ and $\Sigma ^{T}\Sigma $ are diagonal we see that $U$ must contain the eigenvectors of $XX^{T}$ , while $V$ must be the eigenvectors of $X^{T}X$ . Both products have the same non-zero eigenvalues, given by the non-zero entries of $\Sigma \Sigma ^{T}$ , or equally, by the non-zero entries of $\Sigma ^{T}\Sigma $ . Now the decomposition looks like this:

${\begin{matrix}&X&&&U&&\Sigma &&V^{T}\\&({\textbf {d}}_{j})&&&&&&&({\hat {\textbf {d}}}_{j})\\&\downarrow &&&&&&&\downarrow \\({\textbf {t}}_{i}^{T})\rightarrow &{\begin{bmatrix}x_{1,1}&\dots &x_{1,j}&\dots &x_{1,n}\\\vdots &\ddots &\vdots &\ddots &\vdots \\x_{i,1}&\dots &x_{i,j}&\dots &x_{i,n}\\\vdots &\ddots &\vdots &\ddots &\vdots \\x_{m,1}&\dots &x_{m,j}&\dots &x_{m,n}\\\end{bmatrix}}&=&({\hat {\textbf {t}}}_{i}^{T})\rightarrow &{\begin{bmatrix}{\begin{bmatrix}\,\\\,\\{\textbf {u}}_{1}\\\,\\\,\end{bmatrix}}\dots {\begin{bmatrix}\,\\\,\\{\textbf {u}}_{l}\\\,\\\,\end{bmatrix}}\end{bmatrix}}&\cdot &{\begin{bmatrix}\sigma _{1}&\dots &0\\\vdots &\ddots &\vdots \\0&\dots &\sigma _{l}\\\end{bmatrix}}&\cdot &{\begin{bmatrix}{\begin{bmatrix}&&{\textbf {v}}_{1}&&\end{bmatrix}}\\\vdots \\{\begin{bmatrix}&&{\textbf {v}}_{l}&&\end{bmatrix}}\end{bmatrix}}\end{matrix}}$

The values $\sigma _{1},\dots ,\sigma _{l}$ are called the singular values, and $u_{1},\dots ,u_{l}$ and $v_{1},\dots ,v_{l}$ the left and right singular vectors.
Notice the only part of $U$ that contributes to ${\textbf {t}}_{i}$ is the $i{\textrm {'th}}$ row.
Let this row vector be called ${\hat {\textrm {t}}}_{i}^{T}$ .
Likewise, the only part of $V^{T}$ that contributes to ${\textbf {d}}_{j}$ is the $j{\textrm {'th}}$ column, ${\hat {\textrm {d}}}_{j}$ .
These are not the eigenvectors, but depend on all the eigenvectors.
It turns out that when you select the $k$ largest singular values, and their corresponding singular vectors from $U$ and $V$ , you get the rank $k$ approximation to $X$ with the smallest error (Frobenius norm). This approximation has a minimal error.  But more importantly we can now treat the term and document vectors as a "semantic space". The row "term" vector ${\hat {\textbf {t}}}_{i}^{T}$ then has $k$ entries mapping it to a lower-dimensional space. These new dimensions do not relate to any comprehensible concepts. They are a lower-dimensional approximation of the higher-dimensional space. Likewise, the "document" vector ${\hat {\textbf {d}}}_{j}$ is an approximation in this lower-dimensional space. We write this approximation as

$X_{k}=U_{k}\Sigma _{k}V_{k}^{T}$

You can now do the following:
