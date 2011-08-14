# Copyright 2008 Matt Chaput. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#    1. Redistributions of source code must retain the above copyright notice,
#       this list of conditions and the following disclaimer.
#
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY MATT CHAPUT ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO
# EVENT SHALL MATT CHAPUT OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
# EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and documentation are
# those of the authors and should not be interpreted as representing official
# policies, either expressed or implied, of Matt Chaput.

"""
This module contains classes for scoring (and sorting) search results.
"""

from __future__ import division
from math import log, pi

from whoosh.compat import iteritems

# Base classes

class WeightingModel(object):
    """Abstract base class for scoring models. A WeightingModel object provides
    a method, ``scorer``, which returns an instance of
    :class:`whoosh.scoring.Scorer`.
    
    Basically, WeightingModel objects store the configuration information for
    the model (for example, the values of B and K1 in the BM25F model), and
    then creates a scorer instance based on additional run-time information
    (the searcher, the fieldname, and term text) to do the actual scoring.
    """
    
    use_final = False
    
    def idf(self, searcher, fieldname, text):
        """Returns the inverse document frequency of the given term.
        """
        
        n = searcher.doc_frequency(fieldname, text)
        return log((searcher.doc_count_all()) / (n + 1)) + 1
    
    def scorer(self, searcher, fieldname, text, qf=1):
        """Returns an instance of :class:`whoosh.scoring.Scorer` configured
        for the given searcher, fieldname, and term text.
        """
        
        raise NotImplementedError(self.__class__.__name__)
    
    def final(self, searcher, docnum, score):
        """Returns a final score for each document. You can use this method
        in subclasses to apply document-level adjustments to the score, for
        example using the value of stored field to influence the score
        (although that would be slow).
        
        WeightingModel sub-classes that use ``final()`` should have the
        attribute ``use_final`` set to ``True``.
        
        :param searcher: :class:`whoosh.searching.Searcher` for the index.
        :param docnum: the doc number of the document being scored.
        :param score: the document's accumulated term score.
        
        :rtype: float
        """

        return score


class BaseScorer(object):
    """Base class for "scorer" implementations. A scorer provides a method for
    scoring a document, and sometimes methods for rating the "quality" of a
    document and a matcher's current "block", to implement quality-based
    optimizations.
    
    Scorer objects are created by WeightingModel objects. Basically,
    WeightingModel objects store the configuration information for the model
    (for example, the values of B and K1 in the BM25F model), and then creates
    a scorer instance.
    """
    
    def supports_block_quality(self):
        """Returns True if this class supports quality optimizations.
        """
        
        return False
    
    def score(self, matcher):
        """Returns a score for the current document of the matcher.
        """
        
        raise NotImplementedError(self.__class__.__name__)
    
    def block_quality(self, matcher):
        """Returns the *maximum possible score* the matcher can give in its
        current "block" (whatever concept of "block" the backend might use). If
        this score is less than the minimum score required to make the "top N"
        results, then we can tell the matcher to skip ahead to another block
        with better "quality".
        """
        
        raise NotImplementedError(self.__class__.__name__)
    

# WeightScorer

class WeightScorer(BaseScorer):
    """A scorer that simply returns the weight as the score. This is useful
    for more complex weighting models to return when they are asked for a 
    scorer for fields that aren't scorable (don't store field lengths).
    """
    
    def __init__(self, maxweight):
        self.max_quality = maxweight
    
    def supports_block_quality(self):
        return True
    
    def score(self, matcher):
        return matcher.weight()
    
    def block_quality(self, matcher):
        return matcher.block_max_weight()


class WeightLengthScorer(BaseScorer):
    """Base class for scorers where the only per-document variables are term
    weight and field length.
    
    Subclasses should follow this pattern:
    
    * Initializer should take ``searcher, fieldname, text`` as the first three
      arguments. Additional arguments (such as tuning parameters) can be passed
      after these.
      
    * Override the ``_score(weight, length)`` method to return the score for a
      document with the given weight and length.
      
    * Create scorers by calling the ``using`` class method. This method creates
      the scorer object and does routine setup of attributes before returning
      it.
    
    >>> scorer = BM25FScorer.using(searcher, "text", "render", B=0.5)
    >>> scorer.max_weight
    1.25840242
    """
    
    @classmethod
    def using(cls, searcher, fieldname, text, *args, **kwargs):
        """This class method is meant to be called to create and configure a
        new scorer. It initializes the scorer and then does the busy work of
        adding the ``dfl()`` function and ``max_quality`` attributes.
        
        This method assumes the initializers of WeightLengthScorer subclasses
        always take ``searcher, fieldname, text`` as the first three arguments.
        Any additional arguments given to this method are passed through to the
        initializer.
        
        >>> w = BM25
        """
        
        ti = searcher.term_info(fieldname, text)
        
        if not searcher.schema[fieldname].scorable:
            return WeightScorer(ti.max_weight())
        
        obj = cls(searcher, fieldname, text, *args, **kwargs)
        obj.dfl = lambda docnum: searcher.doc_field_length(docnum, fieldname, 1)
        obj.max_quality = obj._score(ti.max_weight(), ti.min_length())
        return obj
    
    def supports_block_quality(self):
        return True
    
    def score(self, matcher):
        return self._score(matcher.weight(), self.dfl(matcher.id()))
    
    def block_quality(self, matcher):
        return self._score(matcher.block_max_weight(),
                           matcher.block_min_length())
        
    def _score(self, weight, length):
        # Override this method with the actual scoring function
        raise NotImplementedError(self.__class__.__name__)
    

# WeightingModel implementations


# BM25F Model

def bm25(idf, tf, fl, avgfl, B, K1):
    # idf - inverse document frequency
    # tf - term frequency in the current document
    # fl - field length in the current document
    # avgfl - average field length across documents in collection
    # B, K1 - free paramters
    score = idf * ((tf * K1 + 1) / (tf + K1 * (1 - B + B * fl / avgfl)))
    return score


class BM25F(WeightingModel):
    """Implements the BM25F scoring algorithm.
    """
    
    def __init__(self, B=0.75, K1=1.2, **kwargs):
        """
        
        >>> from whoosh import scoring
        >>> # Set a custom B value for the "content" field
        >>> w = scoring.BM25F(B=0.75, content_B=1.0, K1=1.5)
        
        :param B: free parameter, see the BM25 literature. Keyword arguments of
            the form ``fieldname_B`` (for example, ``body_B``) set field-
            specific values for B.
        :param K1: free parameter, see the BM25 literature.
        """
        
        self.B = B
        self.K1 = K1
        
        self._field_B = {}
        for k, v in iteritems(kwargs):
            if k.endswith("_B"):
                fieldname = k[:-2]
                self._field_B[fieldname] = v
    
    def supports_block_quality(self):
        return True
    
    def scorer(self, searcher, fieldname, text, qf=1):
        if fieldname in self._field_B:
            B = self._field_B[fieldname]
        else:
            B = self.B
        
        return BM25FScorer.using(searcher, fieldname, text, B, self.K1, qf=qf)


class BM25FScorer(WeightLengthScorer):
    def __init__(self, searcher, fieldname, text, B, K1, qf=1):
        self.idf = searcher.idf(fieldname, text)
        self.avgfl = searcher.avg_field_length(fieldname) or 1
        self.B = B
        self.K1 = K1
        self.qf = qf
        
    def _score(self, weight, length):
        return bm25(self.idf, weight, length, self.avgfl, self.B, self.K1)
            

# DFree model

def dfree(tf, cf, qf, dl, fl):
    # tf - term frequency in current document
    # cf - term frequency in collection
    # qf - term frequency in query
    # dl - field length in current document
    # fl - total field length across all documents in collection
    prior = tf / dl
    post = (tf + 1.0) / (dl + 1.0)
    invpriorcol = fl / cf
    norm = tf * log(post / prior)
    
    return qf * norm * (tf * (log(prior * invpriorcol))
                        + (tf + 1.0) * (log(post * invpriorcol))
                        + 0.5 * log(post / prior))


class DFree(WeightingModel):
    """Implements the DFree scoring model from Terrier.
    
    See http://terrier.org/
    """
    
    def supports_block_quality(self):
        return True
    
    def scorer(self, searcher, fieldname, text, qf=1):
        return DFreeScorer.using(searcher, fieldname, text, qf=qf)


class DFreeScorer(WeightLengthScorer):
    def __init__(self, searcher, fieldname, text, qf=1):
        self.cf = searcher.weight(fieldname, text)
        self.fl = searcher.field_length(fieldname)
        self.qf = qf
    
    def _score(self, weight, length):
        return dfree(weight, self.cf, self.qf, length, self.fl)
        

# PL2 model

rec_log2_of_e = 1.0 / log(2)
def pl2(tf, cf, qf, dc, fl, avgfl, c):
    # tf - term frequency in the current document
    # cf - term frequency in the collection
    # qf - term frequency in the query
    # dc - doc count
    # fl - field length in the current document
    # avgfl - average field length across all documents
    # c -free parameter
    
    TF = tf * log(1.0 + (c * avgfl) / fl)
    norm = 1.0 / (TF + 1.0)
    f = cf / dc
    return norm * qf * (TF * log(1.0 / f)
                        + f * rec_log2_of_e
                        + 0.5 * log(2 * pi * TF)
                        + TF * (log(TF) - rec_log2_of_e))

class PL2(WeightingModel):
    """Implements the PL2 scoring model from Terrier.
    
    See http://terrier.org/
    """
    
    def __init__(self, c=1.0):
        self.c = c
        
    def scorer(self, searcher, fieldname, text, qf=1):
        if not searcher.schema[fieldname].scorable:
            return WeightScorer(searcher.max_weight(fieldname, text))
        
        return PL2Scorer.using(searcher, fieldname, text, self.c, qf=qf)


class PL2Scorer(WeightLengthScorer):
    def __init__(self, searcher, fieldname, text, c, qf=1):
        self.cf = searcher.frequency(fieldname, text)
        self.dc = searcher.doc_count_all()
        self.avgfl = searcher.avg_field_length(fieldname) or 1
        self.c = c
        self.qf = qf
        
    def _score(self, weight, length):
        return pl2(weight, self.cf, self.qf, self.dc, length, self.avgfl,
                   self.c)


# Simple models

class Frequency(WeightingModel):
    def scorer(self, searcher, fieldname, text, qf=1):
        maxweight = searcher.term_info(fieldname, text).max_weight()
        return WeightScorer(maxweight)
    

class TF_IDF(WeightingModel):
    def scorer(self, searcher, fieldname, text, qf=1):
        idf = searcher.idf(fieldname, text)
        maxweight = searcher.term_info(fieldname, text).max_weight()
        return TF_IDF.TF_IDFScorer(maxweight, idf)
    
    class TF_IDFScorer(BaseScorer):
        def __init__(self, maxweight, idf):
            self.max_quality = maxweight * idf
            self.idf = idf
        
        def supports_block_quality(self):
            return True
        
        def score(self, matcher):
            return matcher.weight() * self.idf
        
        def block_quality(self, matcher):
            return matcher.block_maxweight() * self.idf


# Utility models

class Weighting(WeightingModel):
    """This class provides backwards-compatibility with the old weighting
    class architecture, so any existing custom scorers don't need to be
    rewritten.
    
    It may also be useful for quick experimentation since you only need to
    override the ``score()`` method to try a scoring algorithm, without having
    to create an inner Scorer class::
    
        class MyWeighting(Weighting):
            def score(searcher, fieldname, text, docnum, weight):
                # Return the docnum as the score, for some reason
                return docnum
                
        mysearcher = myindex.searcher(weighting=MyWeighting)
    """
    
    def scorer(self, searcher, fieldname, text, qf=1):
        return self.CompatibilityScorer(searcher, fieldname, text, self.score)
    
    def score(self, searcher, fieldname, text, docnum, weight):
        raise NotImplementedError
    
    class CompatibilityScorer(BaseScorer):
        def __init__(self, searcher, fieldname, text, scoremethod):
            self.searcher = searcher
            self.fieldname = fieldname
            self.text = text
            self.scoremethod = scoremethod
        
        def score(self, matcher):
            return self.scoremethod(self.searcher, self.fieldname, self.text,
                                    matcher.id(), matcher.weight())


class MultiWeighting(WeightingModel):
    """Chooses from multiple scoring algorithms based on the field.
    """
    
    def __init__(self, default, **weightings):
        """The only non-keyword argument specifies the default
        :class:`Weighting` instance to use. Keyword arguments specify
        Weighting instances for specific fields.
        
        For example, to use ``BM25`` for most fields, but ``Frequency`` for
        the ``id`` field and ``TF_IDF`` for the ``keys`` field::
        
            mw = MultiWeighting(BM25(), id=Frequency(), keys=TF_IDF())
        
        :param default: the Weighting instance to use for fields not
            specified in the keyword arguments.
        """

        self.default = default
        # Store weighting functions by field name
        self.weightings = weightings
        
    def scorer(self, searcher, fieldname, text, qf=1):
        w = self.weightings.get(fieldname, self.default)
        return w.scorer(searcher, fieldname, text, qf=qf)


class ReverseWeighting(WeightingModel):
    """Wraps a weighting object and subtracts the wrapped model's scores from
    0, essentially reversing the weighting model.
    """
    
    def __init__(self, weighting):
        self.weighting = weighting
    
    def scorer(self, searcher, fieldname, text, qf=1):
        subscorer = self.weighting.scorer(searcher, fieldname, text, qf=qf)
        return ReverseWeighting.ReverseScorer(subscorer)
    
    class ReverseScorer(BaseScorer):
        def __init__(self, subscorer):
            self.subscorer = subscorer
            self.max_quality = 0 - subscorer.max_quality
        
        def supports_block_quality(self):
            return self.subscorer.supports_block_quality()
        
        def score(self, matcher):
            return 0 - self.subscorer.score(matcher)
        
        def block_quality(self, matcher):
            return 0 - self.subscorer.block_quality(matcher)
        

#class PositionWeighting(WeightingModel):
#    def __init__(self, reversed=False):
#        self.reversed = reversed
#        
#    def scorer(self, searcher, fieldname, text, qf=1):
#        return PositionWeighting.PositionScorer()
#    
#    class PositionScorer(BaseScorer):
#        def score(self, matcher):
#            p = min(span.pos for span in matcher.spans())
#            if self.reversed:
#                return p
#            else:
#                return 0 - p
