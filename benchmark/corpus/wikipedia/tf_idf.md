# Tf–idf

Source: https://en.wikipedia.org/wiki/Tf%E2%80%93idf (Wikipedia, CC BY-SA 4.0)

In information retrieval, tf–idf (term frequency–inverse document frequency, TF*IDF, TFIDF, TF–IDF, or Tf–idf) is a measure of importance of a word to a document in a collection or corpus, adjusted for the fact that some words appear more frequently in general. Like the bag-of-words model, it models a document as a multiset of words, without word order. It is a refinement over the simple bag-of-words model, by allowing the weight of words to depend on the rest of the corpus.
It was often used as a weighting factor in searches of information retrieval, text mining, and user modeling. A survey conducted in 2015 showed that 83% of text-based recommender systems in digital libraries used tf–idf. Variations of the tf–idf weighting scheme were often used by search engines as a central tool in scoring and ranking a document's relevance given a user query.
One of the simplest ranking functions is computed by summing the tf–idf for each query term; many more sophisticated ranking functions are variants of this simple model.

## Motivations
Karen Spärck Jones (1972) conceived a statistical interpretation of term-specificity called Inverse Document Frequency (idf), which became a cornerstone of term weighting:

The specificity of a term can be quantified as an inverse function of the number of documents in which it occurs.For example, the df (document frequency) and idf for some words in Shakespeare's 37 plays might be represented as follows:

In the example above, "Romeo", "Falstaff", and "salad" appears in very few plays; since only a handful of plays contain them, their appearance would significantly narrow down the possible list of plays. In contrast, "good" and "sweet" appear in every play and are completely uninformative as to which play it is.

## Definition
The tf–idf is the product of two statistics, term frequency and inverse document frequency. There are various ways for determining the exact values of both statistics.
A formula that aims to define the importance of a keyword or phrase within a document or a web page.

### Term frequency
Term frequency, tf(t,d), is the relative frequency of term t within document d, 

$\mathrm {tf} (t,d)={\frac {f_{t,d}}{\sum _{t'\in d}{f_{t',d}}}}$ ,
where ft,d is the raw count of a term in a document, i.e., the number of times that term t occurs in document d. Note the denominator is simply the total number of terms in document d (counting each occurrence of the same term separately). There are various other ways to define term frequency:

the raw count itself: tf(t,d) = ft,d
Boolean "frequencies": tf(t,d) = 1 if t occurs in d and 0 otherwise;
logarithmically scaled frequency: tf(t,d) = log (1 + ft,d);
augmented frequency, to prevent a bias towards longer documents, e.g. raw frequency divided by the raw frequency of the most frequently occurring term in the document:

$\mathrm {tf} (t,d)=0.5+0.5\cdot {\frac {f_{t,d}}{\max\{f_{t',d}:t'\in d\}}}$

### Inverse document frequency

The inverse document frequency is a measure of how much information the word provides, i.e., how common or rare it is across all documents. It is the logarithmically scaled inverse fraction of the documents that contain the word (obtained by dividing the total number of documents by the number of documents containing the term, and then taking the logarithm of that quotient):

$\mathrm {idf} (t,D)=\log {\frac {N}{n_{t}}}$ with

$D$ : is the set of all documents in the corpus

$N={|D|}$ : total number of documents in the corpus

$n_{t}=|\{d\in D:t\in d\}|$ : number of documents where the term $t$ appears (i.e., $\mathrm {tf} (t,d)\neq 0$ ). If the term is not in the corpus, this will lead to a division-by-zero. It is therefore common to adjust the numerator to $1+N$ and the denominator to $1+n_{t}$ .

### Term frequency–inverse document frequency

Then tf–idf is calculated as

$\mathrm {tfidf} (t,d,D)=\mathrm {tf} (t,d)\cdot \mathrm {idf} (t,D)$

A high weight in tf–idf is reached by a high term frequency (in the given document) and a low document frequency of the term in the whole collection of documents; the weights hence tend to filter out common terms. Since the ratio inside the idf's log function is always greater than or equal to 1, the value of idf (and tf–idf) is greater than or equal to 0. As a term appears in more documents, the ratio inside the logarithm approaches 1, bringing the idf and tf–idf closer to 0.

## Justification of idf
Idf was introduced as "term specificity" by Karen Spärck Jones in a 1972 paper. Although it has worked well as a heuristic, its theoretical foundations have been troublesome for at least three decades afterward, with many researchers trying to find information theoretic justifications for it.
Spärck Jones's own explanation did not propose much theory, aside from a connection to Zipf's law. Attempts have been made to put idf on a probabilistic footing, by estimating the probability that a given document d contains a term t as the relative document frequency,

$P(t|D)={\frac {|\{d\in D:t\in d\}|}{N}},$ so that we can define idf as

${\begin{aligned}\mathrm {idf} &=-\log P(t|D)\\&=\log {\frac {1}{P(t|D)}}\\&=\log {\frac {N}{|\{d\in D:t\in d\}|}}\end{aligned}}$

Namely, the inverse document frequency is the logarithm of "inverse" relative document frequency.
This probabilistic interpretation in turn takes the same form as that of self-information. However, applying such information-theoretic notions to problems in information retrieval leads to problems when trying to define the appropriate event spaces for the required probability distributions: not only documents need to be taken into account, but also queries and terms.

## Link with information theory
Both term frequency and inverse document frequency can be formulated in terms of information theory; it helps to understand why their product has a meaning in terms of joint informational content of a document. A characteristic assumption about the distribution $p(d,t)$ is that:

$p(d|t)={\frac {1}{|\{d\in D:t\in d\}|}}$

This assumption and its implications, according to Aizawa: "represent the heuristic that tf–idf employs."
The conditional entropy of a "randomly chosen" document in the corpus $D$ , conditional to the fact it contains a specific term $t$
 (and assuming that all documents have equal probability to be chosen) is:

$H({\cal {D}}|{\cal {T}}=t)=-\sum _{d}p_{d|t}\log p_{d|t}=-\log {\frac {1}{|\{d\in D:t\in d\}|}}=\log {\frac {|\{d\in D:t\in d\}|}{|D|}}+\log |D|=-\mathrm {idf} (t)+\log |D|$

In terms of notation, ${\cal {D}}$ and ${\cal {T}}$ are "random variables" corresponding to respectively draw a document or a term. The mutual information can be expressed as

$M({\cal {T}};{\cal {D}})=H({\cal {D}})-H({\cal {D}}|{\cal {T}})=\sum _{t}p_{t}\cdot (H({\cal {D}})-H({\cal {D}}|W=t))=\sum _{t}p_{t}\cdot \mathrm {idf} (t)$

The last step is to expand $p_{t}$ , the unconditional probability to draw a term, with respect to the (random) choice of a document, to obtain:

$M({\cal {T}};{\cal {D}})=\sum _{t,d}p_{t|d}\cdot p_{d}\cdot \mathrm {idf} (t)=\sum _{t,d}\mathrm {tf} (t,d)\cdot {\frac {1}{|D|}}\cdot \mathrm {idf} (t)={\frac {1}{|D|}}\sum _{t,d}\mathrm {tf} (t,d)\cdot \mathrm {idf} (t).$

This expression shows that summing the Tf–idf of all possible terms and documents recovers the mutual information between documents and term taking into account all the specificities of their joint distribution. Each Tf–idf hence carries the "bit of information" attached to a term x document pair.

## Link with statistical theory
Tf–idf is closely related to the negative logarithmically transformed p-value from a one-tailed formulation of Fisher's exact test when the underlying corpus documents satisfy certain idealized assumptions. More recently, tf–idf variants were shown to arise as components in the test statistic of a penalized likelihood-ratio test for word burstiness based on a beta-binomial statistical language model. In this framework, the null hypothesis models term occurrences using a binomial distribution, while the alternative hypothesis models word burstiness using a beta-binomial distribution with a gamma-distributed penalty term placed on the beta-binomial precision parameter. The resulting test statistic contains the tf–idf variants binary term frequency–inverse document frequency (btf–idf) and term frequency–inverse collection frequency (tf–icf), thereby establishing a direct connection between tf–idf family term-weighting schemes and statistical hypothesis testing.

## Example of tf–idf
Suppose that we have term count tables of a corpus consisting of only two documents:

"This is a sample A."
"This is another example, another example, example."
