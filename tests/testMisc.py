#
#	testMisc.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	Miscellaneous unit tests
#

import unittest, sys
if '..' not in sys.path:
	sys.path.append('..')
from typing import Tuple
from acme.etc.Types import ResponseStatusCode as RC, ResourceTypes as T
from acme.etc.DateUtils import getResourceDate
from init import *

# TODO move a couple of tests to a http or general request test


class TestMisc(unittest.TestCase):

	@classmethod
	@unittest.skipIf(noCSE, 'No CSEBase')
	def setUpClass(cls) -> None:
		pass


	@classmethod
	@unittest.skipIf(noCSE, 'No CSEBase')
	def tearDownClass(cls) -> None:
		pass

	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_checkHTTPRVI(self) -> None:
		"""	Check RVI parameter in response """
		_, rsc = RETRIEVE(cseURL, ORIGINATOR)
		self.assertEqual(rsc, RC.OK)
		self.assertIn(C.hfRVI, lastHeaders())
		self.assertEqual(lastHeaders()[C.hfRVI], RVI)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_checkHTTPRET(self) -> None:
		"""	Check Request Expiration Timeout in request"""
		_, rsc = RETRIEVE(cseURL, ORIGINATOR, headers={C.hfRET : getResourceDate(10)}) # request expiration in 10 seconds
		self.assertEqual(rsc, RC.OK)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_checkHTTPVSI(self) -> None:
		"""	Check Vendor Information in request"""
		vsi = 'some vendor information'
		r, rsc = RETRIEVE(cseURL, ORIGINATOR, headers={C.hfVSI : vsi})
		self.assertEqual(rsc, RC.OK)
		self.assertEqual(lastHeaders()[C.hfVSI], vsi)


	def test_checkHTTPRETRelative(self) -> None:
		"""	Check Request Expiration Timeout in request (relative)"""
		_, rsc = RETRIEVE(cseURL, ORIGINATOR, headers={C.hfRET : '10000'}) # request expiration in 10 seconds
		self.assertEqual(rsc, RC.OK)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_checkHTTPRETWrong(self) -> None:
		"""	Check Request Expiration Timeout in request (past date) -> Fail"""
		_, rsc = RETRIEVE(cseURL, ORIGINATOR, headers={C.hfRET : getResourceDate(-10)}) # request expiration in 10 seconds
		self.assertEqual(rsc, RC.requestTimeout)


	def test_checkHTTPRETRelativeWrong(self) -> None:
		"""	Check Request Expiration Timeout in request (relative, past date) -> Fail"""
		_, rsc = RETRIEVE(cseURL, ORIGINATOR, headers={C.hfRET : '-10000'}) # request expiration in 10 seconds
		self.assertEqual(rsc, RC.requestTimeout)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_checkHTTPRVIWrongInRequest(self) -> None:
		"""	Check Wrong RVI version parameter in request -> Fail"""
		_, rsc = RETRIEVE(cseURL, ORIGINATOR, headers={C.hfRVI : '1'})
		self.assertEqual(rsc, RC.releaseVersionNotSupported)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_createUnknownResourceType(self) -> None:
		"""	Create an unknown resource type -> Fail """
		dct = 	{ 'foo:bar' : { 
					'rn' : 'foo',
				}}
		r, rsc = CREATE(cseURL, ORIGINATOR, 999, dct)	# type: ignore [arg-type]
		self.assertEqual(rsc, RC.badRequest)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_createEmpty(self) -> None:
		"""	Create with empty content -> Fail """
		dct = 	None
		r, rsc = CREATE(cseURL, ORIGINATOR, T.AE, dct)
		self.assertEqual(rsc, RC.badRequest)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_updateEmpty(self) -> None:
		"""	Update with empty content -> Fail """
		dct = 	None
		r, rsc = UPDATE(cseURL, ORIGINATOR, dct)
		self.assertEqual(rsc, RC.badRequest)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_createAlphaResourceType(self) -> None:
		""" Create a resource with alphanumerical type -> Fail """
		dct = 	{ 'foo:bar' : { 
					'rn' : 'foo',
				}}
		r, rsc = CREATE(cseURL, ORIGINATOR, 'wrong', dct)	# type: ignore # Ignore type of type
		self.assertEqual(rsc, RC.badRequest)


	@unittest.skipIf(noCSE, 'No CSEBase')
	def test_createWithWrongResourceType(self) -> None:
		"""	Create resource with not matching name and type -> Fail """
		dct = 	{ 'm2m:ae' : { 
					'rn' : 'foo',
				}}
		_, rsc = CREATE(cseURL, ORIGINATOR, T.CNT, dct)
		self.assertEqual(rsc, RC.badRequest)


	@unittest.skipIf(noCSE, 'No CSEBase')
	@unittest.skipUnless(BINDING in [ 'http', 'https' ], 'Only when testing with http(s) binding')
	def test_checkHTTPmissingOriginator(self) -> None:
		"""	Check missing originator in request"""
		_, rsc = RETRIEVE(cseURL, None, headers={C.hfRET : getResourceDate(10)}) # request expiration in 10 seconds
		self.assertEqual(rsc, RC.badRequest)


# TODO test for creating a resource with missing type parameter
# TODO test json with comments
# TODO test for ISO8601 format validation

def run(testVerbosity:int, testFailFast:bool) -> Tuple[int, int, int]:
	suite = unittest.TestSuite()
	suite.addTest(TestMisc('test_checkHTTPRVI'))
	suite.addTest(TestMisc('test_checkHTTPRET'))
	suite.addTest(TestMisc('test_checkHTTPVSI'))
	suite.addTest(TestMisc('test_checkHTTPRETRelative'))
	suite.addTest(TestMisc('test_checkHTTPRETWrong'))
	suite.addTest(TestMisc('test_checkHTTPRETRelativeWrong'))
	suite.addTest(TestMisc('test_checkHTTPRVIWrongInRequest'))
	suite.addTest(TestMisc('test_createUnknownResourceType'))
	suite.addTest(TestMisc('test_createEmpty'))
	suite.addTest(TestMisc('test_updateEmpty'))
	suite.addTest(TestMisc('test_createAlphaResourceType'))
	suite.addTest(TestMisc('test_createWithWrongResourceType'))
	suite.addTest(TestMisc('test_checkHTTPmissingOriginator'))

	result = unittest.TextTestRunner(verbosity=testVerbosity, failfast=testFailFast).run(suite)
	printResult(result)
	return result.testsRun, len(result.errors + result.failures), len(result.skipped)

if __name__ == '__main__':
	_, errors, _ = run(2, True)
	sys.exit(errors)
