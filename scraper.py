from bs4 import BeautifulSoup, element
import requests
import time
import re
import nltk
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
import ssl
import time
from socket import error
from config import config as CONFIG, debugger as DEBUG
from test import jobIDs as test_job_ids

# to use your own dataset, change this import to point to your own version of terms.py
from terms import IGNORE, STOP, SAVE_WORDS, SAVE_PHRASES, CONFLATE


# workaround to get nltk to work...
# https://stackoverflow.com/questions/38916452/nltk-download-ssl-certificate-verify-failed
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

nltk.download('punkt', quiet=(not CONFIG.debug_mode))
nltk.download('wordnet', quiet=(not CONFIG.debug_mode))
nltk.download('stopwords', quiet=(not CONFIG.debug_mode))
nltk.download('averaged_perceptron_tagger', quiet=(not CONFIG.debug_mode))

lemmatizer = WordNetLemmatizer()

nltk_stop = set(stopwords.words('english'))
STOP_WORDS = nltk_stop.union(STOP)


# Data to get:
#
# freq of company | ?
# freq of job title | ?
# freq of seniority level | ?
# years of experience freq range | Done
# freq of programming languages | Done
# freq of other tech concepts/key words | Done

class classNames:
    title = 'topcard__title'

    # body of the job description, including requirements and nice-to-haves
    # strong tags indicate headers
    # - might be useful to identify required skills vs nice-to-haves?
    description = 'description__text'

    # criteria list:
    # first: seniority
    # second: employment type (fulltime, contract, etc)
    criteria = 'description__job-criteria-item'


# You can change the search criteria here
KEYWORD = 'Software Developer'
LOCATION = None

SEARCH_URL = 'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={0}'
JOB_URL = 'https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}'

def pause(sec):
    'time.sleep that abides by config rules'
    if CONFIG.enable_pausing:
        time.sleep(sec)

def consoleLog(s):
    'logging to console (print) that abides by config rules'
    if CONFIG.enable_misc_logging:
        print(s)

def getSearchURL(keyword, start = 0, location = None):
    url = SEARCH_URL.format(keyword)

    if (location != None):
        url = url + '&location={}'.format(location)
    
    url = url + '&start={}'.format(start)
    return url

def scrapeLinkedIn(test_IDs = None):

    # display config
    if (CONFIG.debug_mode):
        print('DEBUG MODE: on')
        if (DEBUG.find_terms):
            print('FIND TERMS: on')
        pause(3)

    if test_IDs != None:
        jobIDs = test_IDs
    else:
        jobIDs = getJobIDs()
    
    (years_of_exp, keywords_list, skipped_jobs) = mainWorkflow(jobIDs)

    # if there were any skipped jobs, try re-doing them
    if (len(skipped_jobs) > 0):
        retry = 5
        pause(2)

        consoleLog('Attempting to resolve skipped jobs. Will attempt up to {} times.'.format(retry))
        no_change = 0

        for i in range(retry):
            if (len(skipped_jobs) == 0):
                consoleLog('all jobs searched!')
                break
            if (i > 0):
                consoleLog('restart!')
            consoleLog('iteration {}/{}'.format(i+1, retry))
            pause(0.5)
            last_skipped_count = len(skipped_jobs)
            (skip_years_exp, skip_keywords, skipped_jobs) = mainWorkflow(skipped_jobs)
            if (last_skipped_count == len(skipped_jobs)):
                no_change += 1
                if (no_change >= 2):
                    consoleLog("ending retry process at iteration {} since skipped jobs remains at {}.".format(i+1,last_skipped_count))
                    break
            else:
                no_change = 0
            # merge the years-of-experience dictionaries
            for year in skip_years_exp:
                if year in years_of_exp:
                    years_of_exp[year] += skip_years_exp[year]
                else:
                    years_of_exp[year] = skip_years_exp[year]
            keywords_list = keywords_list + skip_keywords
            consoleLog('pausing...')
            pause(5)
        
        # final summary
        consoleLog('==== final summary (after retry attempts) ====')
        consoleLog('Jobs that couldnt be resolved:')
        consoleLog(skipped_jobs)
        summarizeResults(years_of_exp, keywords_list)
        

def mainWorkflow(jobIDs):
    'performs the main web scraping workflow and returns the data'
    (years_of_exp, keywords_list, summary_info) = scrapeJobs(jobIDs)

    (data_included, len_data, skipped_jobs) = summary_info
    summarizeResults(years_of_exp, keywords_list, data_included, len_data)

    return (years_of_exp, keywords_list, skipped_jobs)

def scrapeJobs(jobIDs):
    'scrapes the data for the given jobIDs'
    skippedJobs = set()

    years_of_exp = {}
    keywords_list = []

    data_included_count = 0
    step = 0
    time_avg = 0
    begin = time.perf_counter()

    for jobID in jobIDs:
        step += 1
        start = time.perf_counter()
        jobData = getJobData(jobID)
        time_avg += (time.perf_counter() - start)
        if jobData[0] is False:
            if (CONFIG.english_only and jobData[1] == 'non-english'):
                if (CONFIG.debug_mode):
                    print('non-english: {}'.format(jobID))
                continue
            if (CONFIG.debug_mode):
                print('no job data found for [{}]'.format(jobID))
                print('reason: {}'.format(jobData[1]))
            skippedJobs.add(jobID)
            continue
        exp = jobData[0]
        keywords = jobData[1]
        data_included_count += 1

        # years of experience
        if (exp in years_of_exp):
            years_of_exp[exp] += 1
        else:
            years_of_exp[exp] = 1
        # keywords
        keywords_list = keywords_list + keywords

        if (step % 10 == 0):
            consoleLog('progress: {}% ({}/{})'.format(round(step/len(jobIDs)*100), step, len(jobIDs)))
            elapsed = round(time.perf_counter() - begin)
            consoleLog('time elapsed: {}s ({}m)'.format(elapsed,round(elapsed / 60)))
    
    if (time_avg > 0):
        time_avg = round(time_avg / len(jobIDs))
        consoleLog('average time per job: {} seconds'.format(time_avg))
    summary_info = (data_included_count, len(jobIDs), skippedJobs)
    return (years_of_exp, keywords_list, summary_info)



def getJobIDs():

    timeLimit = 60

    done = False
    i = 0
    jobIDs = set()

    consoleLog("Getting job IDs")

    while not done:
        #load each page of results, and get all the job IDs from it
        fmtUrl = getSearchURL(KEYWORD, i, LOCATION)
        consoleLog('fetching job IDs from linkedIn at: {}'.format(fmtUrl))
        res = requestURL(fmtUrl)
        # handle for if connection fails for some reason
        if res[0] is False:
            continue
        res = requests.get(fmtUrl)
        soup = BeautifulSoup(res.text, 'html.parser')

        jobDivs = soup.find_all(class_='base-card')

        if (len(jobDivs) == 0):
            done = True
            break

        for div in jobDivs:
            jobID = div.get('data-entity-urn').split(":")[3]
            jobIDs.add(jobID)
        
        i = i + 25 # 25 jobs per results page

    if (CONFIG.debug_mode):
        print(jobIDs)
    return jobIDs

def getJobData(jobID,debug=False):

    fmtUrl = JOB_URL.format(jobID)
    res = requestURL(fmtUrl)
    # handle for if connection fails for some reason
    if res[0] is False:
        return res
    soup = BeautifulSoup(res[1], 'html.parser')
    
    descriptionSection = soup.find(class_=classNames.description)
    if debug:
        print(descriptionSection)
    if (descriptionSection == None):
        return (False, 'couldnt find description section')
    qualifications = getQualifications2(descriptionSection)

    if (qualifications[0] is not False) and CONFIG.debug_mode:
        if qualifications[0] >= 10:
            print('High YOE found: {}y [{}]'.format(qualifications[0], jobID))

    return qualifications


def requestURL(url):
    'Try to fetch the URL, and handle if the connection fails'
    try:
        res = requests.get(url, timeout=10)
    except:
        errmsg = 'connection error: Failed to connect to {}'.format(url)
        if (CONFIG.debug_mode):
            print(errmsg)
        return (False, errmsg)
    
    return (True, res.text)


def getQualifications2(description):

    # clean the description of unwanted tags that might interfere
    for e in description.findAll('br'):
        e.extract()
    for e in description.findAll('strong'):
        e.extract()

    # first try searching for list tags
    output = searchListTags(description)
    if output[0] is not False:
        return output
    
    # if that fails, try searching for p tags
    output = searchPTags(description)
    if output[0] is not False:
        return output

    return (False, 'No data could be scraped from the description...')

def searchListTags(description):
    'some linkedIn job postings are organized by ul/li tags. this searches in those.'
    keyword_set = set()
    max = 0

    # find the list tags in the description
    ul_tag = description.find('ul')
    if ul_tag == None:
        return (False, 'cant find ul tag')
    all_li = ul_tag.findAll('li')
    if (all_li == None):
        return (False, 'cant find li tags...')

    # check bullet points for tech terms and other useful information
    for li in all_li:
        s = findString(li)
        if (s == None):
            if (CONFIG.debug_mode):
                print('empty li tag?')
                print(li)
                print('if there are other tags inside <li>, they should be removed.')
            continue

        if (CONFIG.english_only and isForeignScript(s)):
            return (False, 'non-english')

        # 'year' is present, so this line should be listing years experience
        if 'year' in s:
            n = getMaxNumber(s)
            if n > max:
                max = n
        
        # find keywords
        keywords = stripJunk(s)
        keyword_set = keyword_set.union(keywords)
    
    return (max, list(keyword_set))

def searchPTags(description):
    keyword_set = set()
    max = 0

    ptags = description.findAll('p')
    if ptags == None:
        return (False, 'no P tags could be found.')
    
    for p in ptags:
        s = findString(p)
        if (s == None) or (type(s) != element.NavigableString):
            if (CONFIG.debug_mode):
                print('empty p tag?')
                print(p)
                print('if there are other tags inside <p>, they should be removed')
            continue
        

        if (CONFIG.english_only and isForeignScript(s)):
            return (False, 'non-english')
        
        # 'year' is present, so this line should be listing years experience
        if 'year' in s:
            n = getMaxNumber(s)
            if n > max:
                max = n
        
        # find keywords
        keywords = stripJunk(s)
        keyword_set = keyword_set.union(keywords)
    
    return (max, list(keyword_set))

def findString(tag):
    'try to find a string in the given tag or its children'
    s = tag.string
    if (s != None):
        return s
    
    # if there are children elements, look through them for strings
    if (tag.children != None):
        children = list(tag.children)
        if len(children) >= 1:
            # do search
            s = ''
            for child in children:
                if type(child) == element.NavigableString:
                    s += ' ' + child
                if child.string != None:
                    s += ' ' + child.string
            return s
    
    # otherwise, try the next child - sometimes there are empty tags next to navigable strings (no clue why)
    return tag.nextSibling

def getMaxNumber(s):
    'gets the max number listed in this string'
    tempStr = s.lower()
    max = 0

    stripStr = re.sub('[^0-9]','_', tempStr)
    nums = [n for n in stripStr.split('_') if n != '']
    for n in nums:
        if int(n) > max:
            max = int(n)
    return max
    

# strips all "junk" from an input string and returns the keywords in a set
# expects some form of common language input.
# ex: 
# input ->  "preferred: deep understanding of python, javascript, and mySQL"
# output -> {'python', 'javascript', 'mySQL'}
def stripJunk(s):
    if (s == None):
        return set()
    if (type(s) != element.NavigableString):
        return set()

    # there must be at least some ascii characters, even if non-english
    s = removeNonLatinText(s)
    if len(s) == 0:
        return set()
    
    s = s.lower()
    s = ', or '.join(s.split('/')) # replace / with ', or ' so they are seen as separate terms by NLTK

    # find any existing save words - words we want to intercept and save regardless of what NLTK thinks
    exclude = {',', ':', ';', '!', '(', ')', '[', ']'} # cut out these punc
    temp = ''.join(ch for ch in s if ch not in exclude)
    # find phrases in the string that might include spaces
    # note: this may add performance slowdown since we are doing more iteration and checking for substrings here
    skip = set()
    savePhrases = []
    for phrase in SAVE_PHRASES:
        if phrase in temp:
            skip = skip.union(set(phrase.split())) # don't count this word individually since its part of a phrase
            savePhrases.append(phrase)
    saveWords = [word for word in temp.split() if word in SAVE_WORDS]

    ignore = skip.union(IGNORE)

    # NLTK tries to find nouns (usually pretty well!)
    tokenized = [word for word in nltk.word_tokenize(s) if word not in ignore] # cut ignore words
    clean1 = [word for word in tokenized if word not in STOP]
    tagged = nltk.pos_tag(clean1)
    nouns = [lemmatize(word) for (word, pos) in tagged if 'NN' in pos]

    # combine our saveWords and NLTK's nouns
    # also conflate any similar terms that should be seen as the same thing
    allTheWords = nouns + saveWords + savePhrases
    conflated = []
    for word in allTheWords:
        if (word in ignore):
            continue
        if (word in CONFLATE):
            conflated.append(CONFLATE[word])
        else:
            conflated.append(word)

    output = set(conflated)

    if (CONFIG.debug_mode and DEBUG.find_terms):
        intersect = DEBUG.find_list.intersection(output)
        if len(intersect) > 0:
            print('Found find_list terms!')
            print(intersect)
            print(s)
            input('press enter to continue: ')

    return output

def lemmatize(word):
    'convert word into singular form'
    # prevent non-words ending with 's' from being lemmatized incorrectly
    if len(word) <= 3:
        return word
    return lemmatizer.lemmatize(word)


def removeNonLatinText(s):
    'removes all characters from non-latin scripts (such as Japanese, Arabic, etc)'
    stripStr = re.sub("[^0-9a-zA-Z,.'+#&*-]",'_', s) # replaces all non alphanumeric (or not ,.) with _
    cleanStr = ''
    lastChar = ''
    for ch in stripStr:
        # when a word switches to _, add a space
        if lastChar != '_':
            if ch == '_':
                cleanStr += ' '
        if ch != '_':
            cleanStr += ch
        lastChar = ch
    return cleanStr.strip()

def isForeignScript(s):
    'detects if the given string is a foreign script or not (non-ascii characters)'
    original_length = len(s)
    stripStr = removeNonLatinText(s)

    # if > 50% of the string is comprised of non-ascii characters, it's probably not english.
    if (len(stripStr) < (original_length / 2)):
        return True
    return False

def summarizeResults(years_of_exp, keywords_list, data_included = 0, len_data = 0):
    'shows data gathered from scraping linkedIn, and also displays a summary information of jobs skipped'

    freq_keywords = nltk.FreqDist(keywords_list)
    freq_keywords = [(word, freq) for (word, freq) in freq_keywords.most_common() if freq > CONFIG.keyword_freq]
    if CONFIG.enable_misc_logging:
        print('==== Results ====')
        print('\n')
        print('Top 20 keywords:')
        print('\n')
        for (word, freq) in freq_keywords[:20]:
            print('{}: {}'.format(word, freq))
        print('\n')
        print('(All the rest)')
        print(freq_keywords[20:])
        print('\n')
        print('Frequency of years experience requirements')
        print('\n')
        print(years_of_exp)
        print('\n')

        if (data_included == 0 or len_data == 0):
            return
        print('\n')
        print("== Search info ==")
        print("total jobs found: {}".format(len_data))
        print("jobs searched: {}/{} ({}%)".format(data_included,len_data,round(data_included/len_data*100)))
        print("jobs skipped: {}/{}".format(len_data - data_included,len_data))
    else:
        print(freq_keywords)
                
# functions for testing stuff

def testJob(jobID):
    jobData = getJobData(jobID,debug=True)
    print(jobData)

def testSentence(sentence):
    print(sentence)
    print(stripJunk(element.NavigableString(sentence)))

def manualFindIgnore(word_list):
    ignore_add = set()
    for (word, freq) in word_list:
        if word in SAVE_WORDS:
            continue
        print('{}: {}'.format(word, freq))
        ans = input('add to ignore? (y/n): ')
        if ans.lower() == 'y':
            ignore_add.add(word)
    print('new ignore set: ')
    newIgnore = ignore_add.union(IGNORE)
    print(newIgnore)


#for jobID in skiplist:
#    testJob(jobID)
#    ans = input('enter to continue: ')
#    os.system('clear')

scrapeLinkedIn(test_job_ids) 