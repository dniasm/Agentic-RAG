# Naive Bayes classifier

Source: https://en.wikipedia.org/wiki/Naive_Bayes_classifier (Wikipedia, CC BY-SA 4.0)

In statistics, naive (sometimes simple or idiot's) Bayes classifiers are a family of "probabilistic classifiers" which assume that the features are conditionally independent, given the target class. In other words, a naive Bayes model assumes the information about the class provided by each variable is unrelated to the information from the others, with no information shared between the predictors. The highly unrealistic nature of this assumption, called the naive independence assumption, is what gives the classifier its name. These classifiers are some of the simplest Bayesian network models.
Naive Bayes classifiers generally perform worse than more advanced models like logistic regressions, especially at quantifying uncertainty (with naive Bayes models often producing wildly overconfident probabilities). However, they are highly scalable, requiring only one parameter for each feature or predictor in a learning problem. Maximum-likelihood training can be done by evaluating a closed-form expression (simply by counting observations in each group), rather than the expensive iterative approximation algorithms required by most other models.
Despite the use of Bayes' theorem in the classifier's decision rule, naive Bayes is not (necessarily) a Bayesian method, and naive Bayes models can be fit to data using either Bayesian or frequentist methods.

## Introduction
Naive Bayes is a simple technique for constructing classifiers: models that assign class labels to problem instances, represented as vectors of feature values, where the class labels are drawn from some finite set. There is not a single algorithm for training such classifiers, but a family of algorithms based on a common principle: all naive Bayes classifiers assume that the value of a particular feature is independent of the value of any other feature, given the class variable. For example, a fruit may be considered to be an apple if it is red, round, and about 10 cm in diameter.  A naive Bayes classifier considers each of these features to contribute independently to the probability that this fruit is an apple, regardless of any possible correlations between the color, roundness, and diameter features.
In many practical applications, parameter estimation for naive Bayes models uses the method of maximum likelihood; in other words, one can work with the naive Bayes model without accepting Bayesian probability or using any Bayesian methods.
Despite their naive design and apparently oversimplified assumptions, naive Bayes classifiers have worked quite well in many complex real-world situations. In 2004, an analysis of the Bayesian classification problem showed that there are sound theoretical reasons for the apparently implausible efficacy of naive Bayes classifiers. Still, a comprehensive comparison with other classification algorithms in 2006 showed that Bayes classification is outperformed by other approaches, such as boosted trees or random forests.
An advantage of naive Bayes is that it only requires a small amount of training data to estimate the parameters necessary for classification.

## Probabilistic model
Abstractly, naive Bayes is a conditional probability model: it assigns probabilities $p(C_{k}\mid x_{1},\ldots ,x_{n})$ for each of the K possible outcomes or classes $C_{k}$ given a problem instance to be classified, represented by a vector $\mathbf {x} =(x_{1},\ldots ,x_{n})$ encoding some n features (independent variables).
The problem with the above formulation is that if the number of features n is large or if a feature can take on a large number of values, then basing such a model on probability tables is infeasible. The model must therefore be reformulated to make it more tractable. Using Bayes' theorem, the conditional probability can be decomposed as:

$p(C_{k}\mid \mathbf {x} )={\frac {p(C_{k})\ p(\mathbf {x} \mid C_{k})}{p(\mathbf {x} )}}\,$

In plain English, using Bayesian probability terminology, the above equation can be written as

${\text{posterior}}={\frac {{\text{prior}}\times {\text{likelihood}}}{\text{evidence}}}\,$

In practice, there is interest only in the numerator of that fraction, because the denominator does not depend on $C$ and the values of the features $x_{i}$ are given, so that the denominator is effectively constant.
The numerator is equivalent to the joint probability model

$p(C_{k},x_{1},\ldots ,x_{n})\,$ which can be rewritten as follows, using the chain rule for repeated applications of the definition of conditional probability:

${\begin{aligned}p(C_{k},x_{1},\ldots ,x_{n})&=p(x_{1},\ldots ,x_{n},C_{k})\\&=p(x_{1}\mid x_{2},\ldots ,x_{n},C_{k})\ p(x_{2},\ldots ,x_{n},C_{k})\\&=p(x_{1}\mid x_{2},\ldots ,x_{n},C_{k})\ p(x_{2}\mid x_{3},\ldots ,x_{n},C_{k})\ p(x_{3},\ldots ,x_{n},C_{k})\\&=\cdots \\&=p(x_{1}\mid x_{2},\ldots ,x_{n},C_{k})\ p(x_{2}\mid x_{3},\ldots ,x_{n},C_{k})\cdots p(x_{n-1}\mid x_{n},C_{k})\ p(x_{n}\mid C_{k})\ p(C_{k})\\\end{aligned}}$

Now the "naive" conditional independence assumptions come into play: assume that all features in $\mathbf {x} $ are mutually independent, conditional on the category $C_{k}$ . Under this assumption,

$p(x_{i}\mid x_{i+1},\ldots ,x_{n},C_{k})=p(x_{i}\mid C_{k})\,.$

Thus, the joint model can be expressed as

${\begin{aligned}p(C_{k}\mid x_{1},\ldots ,x_{n})\varpropto \ &p(C_{k},x_{1},\ldots ,x_{n})\\&=p(C_{k})\ p(x_{1}\mid C_{k})\ p(x_{2}\mid C_{k})\ p(x_{3}\mid C_{k})\ \cdots \\&=p(C_{k})\prod _{i=1}^{n}p(x_{i}\mid C_{k})\,,\end{aligned}}$ where $\varpropto $ denotes proportionality since the denominator $p(\mathbf {x} )$ is omitted.
This means that under the above independence assumptions, the conditional distribution over the class variable $C$ is:

$p(C_{k}\mid x_{1},\ldots ,x_{n})={\frac {1}{Z}}\ p(C_{k})\prod _{i=1}^{n}p(x_{i}\mid C_{k})$ where the evidence $Z=p(\mathbf {x} )=\sum _{k}p(C_{k})\ p(\mathbf {x} \mid C_{k})$ is a scaling factor dependent only on $x_{1},\ldots ,x_{n}$ , that is, a constant if the values of the feature variables are known.
Often, it is only necessary to discriminate between classes. In that case, the scaling factor is irrelevant, and it is sufficient to calculate the log-probability up to a factor: $\ln p(C_{k}\mid x_{1},\ldots ,x_{n})=\ln p(C_{k})+\sum _{i=1}^{n}\ln p(x_{i}\mid C_{k})\underbrace {-\ln Z} _{\text{irrelevant}}$
The scaling factor is irrelevant, since discrimination subtracts it away: $\ln {\frac {p(C_{k}\mid x_{1},\ldots ,x_{n})}{p(C_{l}\mid x_{1},\ldots ,x_{n})}}=\left(\ln p(C_{k})+\sum _{i=1}^{n}\ln p(x_{i}\mid C_{k})\right)-\left(\ln p(C_{l})+\sum _{i=1}^{n}\ln p(x_{i}\mid C_{l})\right)$
There are two benefits of using log-probability. One is that it allows an interpretation in information theory, where log-probabilities are units of information in nats. Another is that it avoids arithmetic underflow.

### Constructing a classifier from the probability model
The discussion so far has derived the independent feature model, that is, the naive Bayes probability model. The naive Bayes classifier combines this model with a decision rule. One common rule is to pick the hypothesis that is most probable so as to minimize the probability of misclassification; this is known as the maximum a posteriori or MAP decision rule. The corresponding classifier, a Bayes classifier, is the function that assigns a class label ${\hat {y}}=C_{k}$ for some k as follows:

${\hat {y}}={\underset {k\in \{1,\ldots ,K\}}{\operatorname {argmax} }}\ p(C_{k})\displaystyle \prod _{i=1}^{n}p(x_{i}\mid C_{k}).$

## Parameter estimation and event models
A class's prior may be calculated by assuming equiprobable classes, i.e., $p(C_{k})={\frac {1}{K}}$ , or by calculating an estimate for the class probability from the training set:

          prior for a given class
            no. of samples in that class
            total no. of samples ${\text{prior for a given class}}={\frac {\text{no. of samples in that class}}{\text{total no. of samples}}}\,$

To estimate the parameters for a feature's distribution, one must assume a distribution or generate nonparametric models for the features from the training set.
The assumptions on distributions of features are called the "event model" of the naive Bayes classifier. For discrete features like the ones encountered in document classification (include spam filtering), multinomial and Bernoulli distributions are popular. These assumptions lead to two distinct models, which are often confused.
