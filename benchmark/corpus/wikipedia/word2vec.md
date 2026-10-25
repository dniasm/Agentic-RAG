# Word2vec

Source: https://en.wikipedia.org/wiki/Word2Vec (Wikipedia, CC BY-SA 4.0)

In natural language processing, Word2Vec is a technique for obtaining vector representations of words as word embeddings. These vectors capture information about the meaning of a word based on its surrounding words in a piece of text, following the principles of distributional semantics. Once trained, the model can be used to find words with similar meanings or usage, while its embeddings can serve as inputs to systems for search and classification.
Word2Vec was developed by Tomáš Mikolov, Kai Chen, Greg Corrado, Ilya Sutskever and Jeff Dean at Google, published in preprints and presented at ICLR in 2013. Its computational efficiency made it practical to learn high-quality word embeddings from very large text corpora and contributed to their widespread adoption in natural language processing.

## Approach
Word2Vec is a group of related models that are used to produce word embeddings. These models are shallow, two-layer neural networks that are trained to reconstruct linguistic contexts of words. Word2Vec takes as its input a large corpus of text and produces a mapping of the set of words to a vector space, typically of several hundred dimensions, with each unique word in the corpus being assigned a vector in the space.
Word2Vec can use either of two model architectures to produce these distributed representations of words: continuous bag of words (CBOW) or continuously sliding skip-gram. In both architectures, Word2Vec considers both individual words and a sliding context window as it iterates over the corpus. 
The CBOW can be viewed as a "fill in the blank" task, where the word embedding represents the way the word influences the relative probabilities of other words in the context window. Words which are semantically similar should influence these probabilities in similar ways, because semantically similar words should be used in similar contexts. The order of context words does not influence prediction (bag of words assumption). 
In the continuous skip-gram architecture, the model uses the current word to predict the surrounding window of context words. The skip-gram architecture weighs nearby context words more heavily than more distant context words. According to the authors' note, CBOW is faster while skip-gram does a better job for infrequent words.
After the model is trained, the learned word embeddings are positioned in the vector space such that words that share common contexts in the corpus — that is, words that are semantically and syntactically similar — are located close to one another in the space. More dissimilar words are located farther from one another in the space.

## Mathematical details
This section is based on expositions.
A corpus is a sequence of words. Both CBOW and skip-gram are methods to learn one vector per word appearing in the corpus.
Let $V$
 ("vocabulary") be the set of all words appearing in the corpus $C$ . Our goal is to learn one vector $v_{w}\in \mathbb {R} ^{d}$ for each word $w\in V$ .
The idea of skip-gram is that the vector of a word should be close to the vector of each of its neighbors. The idea of CBOW is that the vector-sum of a word's neighbors should be close to the vector of the word.

### Continuous bag-of-words (CBOW)

The idea of CBOW is to represent each word with a vector, such that it is possible to predict a word using the sum of the vectors of its neighbors. Specifically, for each word $w_{i}$ in the corpus, the one-hot encoding of the word is used as the input to the neural network. The output of the neural network is a probability distribution over the dictionary, representing a prediction of individual words in the neighborhood of $w_{i}$ . The objective of training is to maximize $\sum _{i}\ln \Pr(w_{i}\mid w_{i+j}\colon j\in N)$ where $N$ is a set of (non-zero) indices representing the relative locations of nearby words considered to be in $w_{i}$
's neighborhood.
For example, if we want each word in the corpus to be predicted by every other word in a small span of 4 words. The set of relative indexes of neighbor words will be: $N=\{-2,-1,+1,+2\}$ , and the objective is to maximize $\sum _{i}\ln \Pr(w_{i}\mid w_{i-2},w_{i-1},w_{i+1},w_{i+2})$ .
In standard bag-of-words, a word's context is represented by a word-count (aka a word histogram) of its neighboring words. For example, the "sat" in "the cat sat on the mat" is represented as {"the": 2, "cat": 1, "on": 1}. Note that the last word "mat" is not used to represent "sat", because it is outside the neighborhood $N=\{-2,-1,+1,+2\}$ .
In continuous bag-of-words, the histogram is multiplied by a matrix $V$ to obtain a continuous representation of the word's context. The matrix $V$ is also called a dictionary. Its columns are the word vectors. It has $D$ columns, where $D$ is the size of the dictionary. Let $d$ be the length of each word vector. We have $V\in \mathbb {R} ^{d\times D}$ .
For example, multiplying the word histogram {"the": 2, "cat": 1, "on": 1} with $V$ , we obtain $2v_{\text{the}}+v_{\text{cat}}+v_{\text{on}}$ .
This is then multiplied with another matrix $V'$ of shape $\mathbb {R} ^{D\times d}$ . Each row of it is a word vector $v'$ . This results in a vector of length $D$ , one entry per dictionary entry. Then, apply the softmax to obtain a probability distribution over the dictionary.
This system can be visualized as a neural network, similar in spirit to an autoencoder, of architecture linear-linear-softmax, as depicted in the diagram. The system is trained by gradient descent to minimize the cross-entropy loss. 
In full formula, the cross-entropy loss is: $-\sum _{i}\ln {\frac {e^{v_{w_{i}}'\cdot (\sum _{j\in N}v_{w_{j+i}})}}{\sum _{w'}e^{v_{w'}'\cdot (\sum _{j\in N}v_{{w'}_{j+i}})}}}$ where the outer summation $\sum _{i}$ is over the words in a corpus, the quantity $\sum _{j\in N}v_{w_{j+i}}$ is the sum of a word's neighbors' vectors, etc.
Once such a system is trained, we have two trained matrices $V,V'$ . Either the column vectors of $V$ or the row vectors of $V'$ can serve as the dictionary. For example, the word "sat" can be represented as either the "sat"-th column of $V$ or the "sat"-th row of $V'$ . It is also possible to simply define $V'=V^{\top }$ , in which case there would no longer be a choice.

### Skip-gram

The idea of skip-gram is to represent each word with a vector, such that it is possible to predict the vectors of its neighbors using the vector of a word.
The architecture is still linear-linear-softmax, the same as CBOW, but the input and the output are switched. Specifically, for each word $w_{i}$ in the corpus, the one-hot encoding of the word is used as the input to the neural network. The output of the neural network is a probability distribution over the dictionary, representing a prediction of individual words in the neighborhood of $w_{i}$ . The objective of training is to maximize $\sum _{i}\sum _{j\in N}\ln \Pr(w_{j+i}\mid w_{i})$ . 
In full formula, the loss function is $-\sum _{i}\sum _{j\in N}\ln {\frac {e^{v_{w_{j+i}}'\cdot v_{w_{i}}}}{\sum _{w'}e^{v_{w'}'\cdot v_{w_{i}}}}}$
Same as CBOW, once such a system is trained, we have two trained matrices $V,V'$ . Either the column vectors of $V$ or the row vectors of $V'$ can serve as the dictionary. It is also possible to simply define $V'=V^{\top }$ , in which case there would no longer be a choice. 
Essentially, skip-gram and CBOW are exactly the same in architecture. They only differ in the objective function during training.
