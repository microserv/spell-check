import client
import norvig_spellcheck
from twisted.internet.defer import DeferredList,Deferred
import json
import time

import CONFIG

def generate_keytree(freqdict):
    """A recursive structure where each letter maps to a tuple (int, dict_of_letters) 
       where int=1 if the tree thus far forms a recognized word
       
       Dict of letters contains only letters that succeed the current tree of letters
       """
    D = {}
    for word in freqdict:
        d = D
        for letter in word:
            letter = letter.lower()
            if letter in d:
                current = d[letter]
                d = current[1]
            else:
                current = [0, {}]
                d[letter] = current
                d = current[1]
        current[0] = 1
    return D
def check_prefix(prefix, keytree):
    """Recursively look up a prefix in the given keytree, return all completions of prefix"""
    D = keytree
    d = D
    L = []
    ex = 0
    #scan until prefix
    for (i,letter) in enumerate(prefix,1):
        letter = letter.lower()
        if letter in d:
            ex,d = d[letter]
            #if ex > 0:
            #    L.append(prefix[0:i].lower())
        else:
            return []
    if ex > 0:
        #prefix is a recognized word
        L.append(prefix.lower())
    #find matches after prefix
    def dfs(d,C):
        for key in d:
            C2 = C[:] + [key]
            ex,d2 = d[key]
            if ex > 0:
                L.append(prefix.lower() + ''.join(C2).lower())
            dfs(d2,C2)
    dfs(d,[])
    return L


def index_frequencies(server):
    """Fetch specialized frequencies from index"""
    print("Fetching frequencies from index")
    indexquery = {'task':'getFrequencyList'}
    d_request = client.send_query(indexquery, CONFIG.index_host)
    d_request.addCallback(lambda x: (server.__dict__.__setitem__('keytree_search', x),x)[1])
    server.timestamp = time.time()
    return d_request

def index_completion(query):
    """Fetch list of completions for query word from index"""
    indexquery = {'task':'getSuggestions', 'word': query}
    d_request = client.send_query(indexquery, CONFIG.index_host)
    
    return d_request

class Spelling(object):
    '''prepare returns a dictionary with the result of the spellcheck'''
    def __init__(self, d,server):
        self.type = d['Type']
        self.query = d['Query']
        self.is_search = d['Search']

        self.server = server
        
        #Minimum length of query for completion to be attempted.
        #A single letter returns too many results and is far too unspecific.
        self.completion_query_minlen = 3

    def get_frequencies(self):
        return self.server.freqs
        
    def complete(self, result_list,frequency_dict,lim=10,keytree=None):
        """Return a ranked list of completions, given: 
           a result_list of completions for the query word,
           a frequency_dict of word:frequency mappings. Either generic local, or specialized from index
           optionally a limit of words to return,
           optionally a keytree to speed up completions 
        """
        
        #No keytree, do completion by iterating through all words in frequency_dict
        if keytree == None:
            ql = self.query.lower()
            L = []
            for key in frequency_dict:
                if key.lower().startswith(ql):
                    L.append(key)
        #Use keytree to speed up completions
        else:
            L = check_prefix(self.query.lower(), self.server.keytree)

        #Sort results by frequency, descending. Slice to return max lim 
        sorted_results = sorted(L, key=lambda x:frequency_dict.get(x,1), reverse=True)[:10]        
        return sorted_results

    def complete_deferreds(self, RF):
            """Wait for deferreds"""
            result_s = RF[0][1]
            frequency_s = RF[1][1]
            
            result = json.loads(result_s)
            frequency_dict = json.loads(frequency_s)
            result_list = result['suggestions']
            return self.complete(result_list, frequency_dict, 10)



    def correct(self, freqs, query):
        """Using edit distance, return a list of suggestions for correcting the query word
           Rank list based on frequencies.
        """
        if type(freqs) != dict:
          freqs = json.loads(freqs[0][1])
        suggestions = norvig_spellcheck.correct(query, freqs)
        return suggestions
        
    def spellcheck(self):
        """Main for spellcheck. """
        USE_INDEX_FOR_SEARCH = self.is_search

        #Do not do anything about stopwords
        if self.query.lower() in self.server.stopwords:
            print('STOPWORD: {}'.format(self.query.lower()))
            return [self.query.lower()]
            
        #do search-specific (using article keywords/frequencies) spellcheck
        if USE_INDEX_FOR_SEARCH:
            x = (time.time() - self.server.timestamp) < self.server.TTL
            if self.server.keytree_search and (time.time() - self.server.timestamp) < self.server.TTL: #use cached version if exists and not older than timestamp
                d_freqs = Deferred()
                d_freqs.callback(self.server.keytree_search)
            else:
                d_freqs = index_frequencies(self.server)
            if self.type.lower() == 'completion':
                d_result = index_completion(self.query.lower())
                
                callbacks = DeferredList([d_result, d_freqs])
                x = callbacks.addCallback(self.complete_deferreds)
                
                result = callbacks
            elif self.type.lower() == 'correction':
                callbacks = DeferredList([d_freqs])
                callbacks.addCallback(lambda x:self.correct(x,self.query.lower()))

                result = callbacks
        #do generic spellcheck
        else:
            freqs = self.get_frequencies()
            if self.type.lower() == 'completion':
                if len(self.query) < self.completion_query_minlen:
                    return []
                results = freqs.keys()
                return self.complete(results, freqs, 10, self.server.keytree)

            elif self.type.lower() == 'correction':
                freqs = self.get_frequencies()
                results = self.correct(freqs, self.query.lower())
                return results
        return result
        
         

