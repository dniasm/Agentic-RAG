# N-gram

Source: https://en.wikipedia.org/wiki/N-gram (Wikipedia, CC BY-SA 4.0)

An n-gram is a sequence of n adjacent symbols in a particular order. The symbols may be n adjacent letters (including punctuation marks and blanks), syllables, or rarely whole words found in a language dataset; or adjacent phonemes extracted from a speech-recording dataset, or adjacent base pairs extracted from a genome. They are collected from a text corpus or speech corpus.  
If Latin numerical prefixes are used, then n-gram of size 1 is called a "unigram", size 2 a "bigram" (or, less commonly, a "digram") etc. If, instead of the Latin ones, the English cardinal numbers are furtherly used, then they are called "four-gram", "five-gram", etc. Similarly, Greek numerical prefixes such as "monomer", "dimer", "trimer", "tetramer", "pentamer", etc., or English cardinal numbers, "one-mer", "two-mer", "three-mer", etc. are used in computational biology for polymers or oligomers of a known size, called k-mers. When the items are words, n-grams may also be called shingles.
In the context of natural language processing (NLP), the use of n-grams allows bag-of-words models to capture information such as word order, which would not be possible in the traditional bag of words setting.

## Examples
In 1951, Shannon discussed n-gram models of English. For example:

3-gram character model (random draw based on the probabilities of each trigram): in no ist lat whey cratict froure birs grocid pondenome of demonstures of the retagin is regiactiona of cre
2-gram word model (random draw of words taking into account their transition probabilities): the head and in frontal attack on an english writer that the character of this point is therefore another method for the letters that the time of who ever told the problem for an unexpected

Figure 1 shows several example sequences and the corresponding 1-gram, 2-gram and 3-gram sequences.
Here are further examples; these are word-level 3-grams and 4-grams (and counts of the number of times they appeared) from the Google n-gram corpus.
3-grams

ceramics collectables collectibles (55)
ceramics collectables fine (130)
ceramics collected by (52)
ceramics collectible pottery (50)
ceramics collectibles cooking (45)
4-grams

serve as the incoming (92)
serve as the incubator (99)
serve as the independent (794)
serve as the index (223)
serve as the indication (72)
serve as the indicator (120)
