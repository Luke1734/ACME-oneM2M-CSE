#
#	HttpServer.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	Server to implement the http part of the oneM2M Mcx communication interface.
#

from __future__ import annotations
import logging, sys, urllib3, json
from requests.api import head
from ssl import OP_ALL
from copy import deepcopy
from typing import Any, Callable, cast, Tuple


import flask
from flask import Flask, Request, make_response, request
from werkzeug.wrappers import Response
from werkzeug.serving import WSGIRequestHandler
from werkzeug.datastructures import MultiDict
import requests

from ..etc.Constants import Constants as C
from ..etc.Types import ReqResp, ResourceTypes as T, Result, ResponseStatusCode as RC, JSON
from ..etc.Types import Operation, CSERequest, ContentSerializationType, Parameters
from ..etc import Utils as Utils, RequestUtils as RequestUtils
from ..services.Configuration import Configuration
from ..services import CSE as CSE
from ..services.Logging import Logging as L, LogLevel
from ..services.MQTTClient import dissectMQTTRequest
from ..webui.webUI import WebUI
from ..resources.Resource import Resource
from ..helpers import TextTools as TextTools
from ..helpers.BackgroundWorker import *
from ..etc import DateUtils


#
# Types definitions for the http server
#

FlaskHandler = 	Callable[[str], Response]
""" Type definition for flask handler. """


class HttpServer(object):

	def __init__(self) -> None:

		# Initialize the http server
		# Meaning defaults are automatically provided.
		self.flaskApp			= Flask(CSE.cseCsi)
		self.rootPath			= Configuration.get('http.root')
		self.serverAddress		= Configuration.get('http.address')
		self.listenIF			= Configuration.get('http.listenIF')
		self.port 				= Configuration.get('http.port')
		self.webuiRoot 			= Configuration.get('cse.webui.root')
		self.webuiDirectory 	= f'{Configuration.get("packageDirectory")}/webui'
		self.isStopped			= False


		self.backgroundActor:BackgroundWorker = None

		self.serverID			= f'ACME {C.version}' 			# The server's ID for http response headers
		self._responseHeaders	= {'Server' : self.serverID}	# Additional headers for other requests

		L.isInfo and L.log(f'Registering http server root at: {self.rootPath}')
		if CSE.security.useTLSHttp:
			L.isInfo and L.log('TLS enabled. HTTP server serves via https.')


		# Add endpoints

		self.addEndpoint(self.rootPath + '/<path:path>', handler=self.handleGET, methods=['GET'])
		self.addEndpoint(self.rootPath + '/<path:path>', handler=self.handlePOST, methods=['POST'])
		self.addEndpoint(self.rootPath + '/<path:path>', handler=self.handlePUT, methods=['PUT'])
		self.addEndpoint(self.rootPath + '/<path:path>', handler=self.handleDELETE, methods=['DELETE'])

		self.addEndpoint(self.rootPath + '/<path:path>', handler=self.handlePATCH, methods=['PATCH'])

		# Register the endpoint for the web UI
		# This is done by instancing the otherwise "external" web UI
		self.webui = WebUI(self.flaskApp, 
						   defaultRI=CSE.cseRi, 
						   defaultOriginator=CSE.cseOriginator, 
						   root=self.webuiRoot,
						   webuiDirectory=self.webuiDirectory,
						   version=C.version)

		# Enable the config endpoint
		if Configuration.get('http.enableRemoteConfiguration'):
			configEndpoint = f'{self.rootPath}/__config__'
			L.isInfo and L.log(f'Registering configuration endpoint at: {configEndpoint}')
			self.addEndpoint(configEndpoint, handler=self.handleConfig, methods=['GET'], strictSlashes=False)
			self.addEndpoint(f'{configEndpoint}/<path:path>', handler=self.handleConfig, methods=['GET', 'PUT'])

		# Enable the config endpoint
		if Configuration.get('http.enableStructureEndpoint'):
			structureEndpoint = f'{self.rootPath}/__structure__'
			L.isInfo and L.log(f'Registering structure endpoint at: {structureEndpoint}')
			self.addEndpoint(structureEndpoint, handler=self.handleStructure, methods=['GET'], strictSlashes=False)
			self.addEndpoint(f'{structureEndpoint}/<path:path>', handler=self.handleStructure, methods=['GET', 'PUT'])

		# Enable the reset endpoint
		if Configuration.get('http.enableResetEndpoint'):
			resetEndPoint = f'{self.rootPath}/__reset__'
			L.isInfo and L.log(f'Registering reset endpoint at: {resetEndPoint}')
			self.addEndpoint(resetEndPoint, handler=self.handleReset, methods=['GET'], strictSlashes=False)

		# Enable the upper tester endpoint
		if Configuration.get('http.enableUpperTesterEndpoint'):
			upperTesterEndpoint = f'{self.rootPath}/__ut__'
			L.isInfo and L.log(f'Registering upper tester endpoint at: {upperTesterEndpoint}')
			self.addEndpoint(upperTesterEndpoint, handler=self.handleUpperTester, methods=['POST'], strictSlashes=False)


		# Add mapping / macro endpoints
		self.mappings = {}
		if mappings := Configuration.get('server.http.mappings'):
			# mappings is a list of tuples
			for (k, v) in mappings:
				L.isInfo and L.log(f'Registering mapping: {self.rootPath}{k} -> {self.rootPath}{v}')
				self.addEndpoint(self.rootPath + k, handler=self.requestRedirect, methods=['GET', 'POST', 'PUT', 'DELETE'])
			self.mappings = dict(mappings)

		# Disable most logs from requests and urllib3 library 
		logging.getLogger("requests").setLevel(LogLevel.WARNING)
		logging.getLogger("urllib3").setLevel(LogLevel.WARNING)
		if not CSE.security.verifyCertificateHttp:	# only when we also verify  certificates
			urllib3.disable_warnings()
		L.isInfo and L.log('HTTP Server initialized')



	def run(self) -> None:
		"""	Run the http server in a separate thread.
		"""
		self.httpActor = BackgroundWorkerPool.newActor(self._run, name='HTTPServer')
		self.httpActor.start()
	

	def shutdown(self) -> bool:
		"""	Shutting down the http server.
		"""
		L.isInfo and L.log('HttpServer shut down')
		self.isStopped = True
		return True
	

	def pause(self) -> None:
		"""	Stop handling requests.
		"""
		L.isInfo and L.log('HttpServer paused')
		self.isStopped = True
		
	
	def unpause(self) -> None:
		"""	Continue handling requests.
		"""
		L.isInfo and L.log('HttpServer unpaused')
		self.isStopped = False

	
	def _run(self) -> None:
		WSGIRequestHandler.protocol_version = "HTTP/1.1"

		# Run the http server. This runs forever.
		# The server can run single-threadedly since some of the underlying
		# components (e.g. TinyDB) may run into problems otherwise.
		if self.flaskApp:
			# Disable the flask banner messages
			cli = sys.modules['flask.cli']
			cli.show_server_banner = lambda *x: None 	# type: ignore
			# Start the server
			try:
				self.flaskApp.run(host=self.listenIF, 
								  port=self.port,
								  threaded=True,
								  request_handler=ACMERequestHandler,
								  ssl_context=CSE.security.getSSLContext(),
								  debug=False)
			except Exception as e:
				# No logging for headless, nevertheless print the reason what happened
				if CSE.isHeadless:
					L.console(str(e), isError=True)
				L.logErr(str(e))
				CSE.shutdown() # exit the CSE. Cleanup happens in the CSE atexit() handler


	def addEndpoint(self, endpoint:str=None, endpoint_name:str=None, handler:FlaskHandler=None, methods:list[str]=None, strictSlashes:bool=True) -> None:
		self.flaskApp.add_url_rule(endpoint, endpoint_name, handler, methods=methods, strict_slashes=strictSlashes)


	def _handleRequest(self, path:str, operation:Operation) -> Response:
		"""	Get and check all the necessary information from the request and
			build the internal strutures. Then, depending on the operation,
			call the associated request handler.
		"""
		L.isDebug and L.logDebug(f'==> HTTP Request: {path}') 	# path = request.path  w/o the root
		L.isDebug and L.logDebug(f'Operation: {operation.name}')
		L.isDebug and L.logDebug(f'Headers: \n{str(request.headers).rstrip()}')
		dissectResult = self._dissectHttpRequest(request, operation, path)

		# log Body, if there is one
		if operation in [ Operation.CREATE, Operation.UPDATE, Operation.NOTIFY ] and dissectResult.request.originalData:
			if dissectResult.request.ct == ContentSerializationType.JSON:
				L.isDebug and L.logDebug(f'Body: \n{str(dissectResult.request.originalData)}')
			else:
				L.isDebug and L.logDebug(f'Body: \n{TextTools.toHex(cast(bytes, dissectResult.request.originalData))}\n=>\n{dissectResult.request.pc}')

		# Send and error message when the CSE is shutting down, or the http server is stopped
		if self.isStopped:
			# Return an error if the server is stopped
			return self._prepareResponse(Result(rsc=RC.internalServerError, request=dissectResult.request, dbg='http server not running', status=False))
		if not dissectResult.status:
			# Something went wrong during dissection
			return self._prepareResponse(dissectResult)

		try:
			responseResult = CSE.request.handleRequest(dissectResult.request)
		except Exception as e:
			responseResult = Utils.exceptionToResult(e)
		return self._prepareResponse(responseResult, dissectResult.request)


	def handleGET(self, path:str=None) -> Response:
		Utils.renameCurrentThread()
		CSE.event.httpRetrieve() # type: ignore [attr-defined]
		return self._handleRequest(path, Operation.RETRIEVE)


	def handlePOST(self, path:str=None) -> Response:
		Utils.renameCurrentThread()
		if self._hasContentType():
			CSE.event.httpCreate()		# type: ignore [attr-defined]
			return self._handleRequest(path, Operation.CREATE)
		else:
			CSE.event.httpNotify()	# type: ignore [attr-defined]
			return self._handleRequest(path, Operation.NOTIFY)


	def handlePUT(self, path:str=None) -> Response:
		Utils.renameCurrentThread()
		CSE.event.httpUpdate()	# type: ignore [attr-defined]
		return self._handleRequest(path, Operation.UPDATE)


	def handleDELETE(self, path:str=None) -> Response:
		Utils.renameCurrentThread()
		CSE.event.httpDelete()	# type: ignore [attr-defined]
		return self._handleRequest(path, Operation.DELETE)


	def handlePATCH(self, path:str=None) -> Response:
		"""	Support instead of DELETE for http/1.0.
		"""
		if request.environ.get('SERVER_PROTOCOL') != 'HTTP/1.0':
			L.logWarn(dbg := 'PATCH method is only allowed for HTTP/1.0. Rejected.')
			return Response(dbg, status=405)
		Utils.renameCurrentThread()
		CSE.event.httpDelete()	# type: ignore [attr-defined]
		return self._handleRequest(path, Operation.DELETE)


	#########################################################################


	# Handle requests to mapped paths
	def requestRedirect(self, path:str=None) -> Response:
		if self.isStopped:
			return Response('Service not available', status=503)
		path = request.path[len(self.rootPath):] if request.path.startswith(self.rootPath) else request.path
		if path in self.mappings:
			L.isDebug and L.logDebug(f'==> Redirecting to: /{path}')
			CSE.event.httpRedirect()	# type: ignore
			return flask.redirect(self.mappings[path], code=307)
		return Response('', status=404)


	#########################################################################
	#
	#	Various handlers
	#


	# Redirect request to / to webui
	def redirectRoot(self) -> Response:
		"""	Redirect a request to the webroot to the web UI.
		"""
		if self.isStopped:
			return Response('Service not available', status=503)
		return flask.redirect(self.webuiRoot, code=302)


	def getVersion(self) -> Response:
		"""	Handle a GET request to return the CSE version.
		"""
		if self.isStopped:
			return Response('Service not available', status=503)
		return Response(C.version, headers=self._responseHeaders)


	def handleConfig(self, path:str=None) -> Response:
		"""	Handle a configuration request. This can either be e GET request to query a 
			configuration value, or a PUT request to set a new value to a configuration setting.
			Note, that only a few of configuration settings are supported.
		"""
		if self.isStopped:
			return Response('Service not available', status=503)

		def _r(r:str) -> Response:	# just construct a response. Trying to reduce the clutter here
			return Response(r, headers=self._responseHeaders)

		if request.method == 'GET':
			if path == None or len(path) == 0:
				return _r(Configuration.print())
			if Configuration.has(path):
				return _r(str(Configuration.get(path)))
			return _r('')
		elif request.method =='PUT':
			data = request.data.decode('utf-8').rstrip()
			try:
				L.isDebug and L.logDebug(f'New remote configuration: {path} = {data}')
				if path == 'cse.checkExpirationsInterval':
					if (d := int(data)) < 1:
						return _r('nak')
					Configuration.set(path, d)
					CSE.registration.stopExpirationMonitor()
					CSE.registration.startExpirationMonitor()
					return _r('ack')
				elif path in [ 'cse.req.minet', 'cse.req.maxnet' ]:	# int configs
					if (d := int(data)) < 1:
							return _r('nak')
					Configuration.set(path, d)
					return _r('ack')
				elif path == 'cse.requestExpirationDelta':	# float configs
					if (f := float(data)) <= 0.0:
							return _r('nak')
					Configuration.set(path, f)
					CSE.request.requestExpirationDelta = f
					return _r('ack')
			except:
				return _r('nak')
			return _r('nak')
		return _r('unsupported')


	def handleStructure(self, path:str='puml') -> Response:
		"""	Handle a structure request. Return a description of the CSE's current resource
			and registrar / registree deployment.
			An optional parameter 'lvl=<int>' can limit the generated resource tree's depth.
		"""
		if self.isStopped:
			return Response('Service not available', status=503)
		lvl = request.args.get('lvl', default=0, type=int)
		if path == 'puml':
			return Response(response=CSE.statistics.getStructurePuml(lvl), headers=self._responseHeaders)
		if path == 'text':
			return Response(response=CSE.console.getResourceTreeText(lvl), headers=self._responseHeaders)
		return Response(response='unsupported', status=422, headers=self._responseHeaders)


	def handleReset(self, path:str=None) -> Response:
		"""	Handle a CSE reset request.
		"""
		if self.isStopped:
			return Response('Service not available', status=503)
		CSE.resetCSE()
		return Response(response='', status=200)


	#########################################################################

	# 
	#	Upper Tester Handler
	#

	def handleUpperTester(self, path:str=None) -> Response:
		"""	Handle a Upper Tester request. See TS-0019 for details.
		"""
		if self.isStopped:
			return Response('Service not available', status=503)

		def prepareUTResponse(rcs:RC) -> Response:
			"""	Prepare the Upper Tester Response.
			"""
			headers = {}
			headers['Server'] = self.serverID
			headers['X-M2M-RSC'] = str(rcs.value)
			return Response(status = 200 if rcs == RC.OK else 400, headers = headers)


		Utils.renameCurrentThread()
		L.isDebug and L.logDebug(f'==> Upper Tester Request:') 
		L.isDebug and L.logDebug(f'Headers: \n{str(request.headers).rstrip()}')
		L.isDebug and L.logDebug(f'Body: \n{request.json}')

		# Handle special commands
		if (cmd := request.headers.get('X-M2M-UTCMD')) is not None:
			if cmd.lower() == 'reset':
				CSE.resetCSE()
				return prepareUTResponse(RC.OK)
			return prepareUTResponse(RC.badRequest)
		
		L.logWarn('UT functionality is not fully supported.')
		return prepareUTResponse(RC.badRequest)

		
		# TODO implement further functionality of Upper Tester spec
		
		# Otherwise	process this as a oneM2M request

		# if request.content_type != 'application/json':
		# 	# return Response(response = f'Unsupported Content-Type: {request.content_type}', status = 400)
		# 	return prepareUTResponse(RC.badRequest)
		# if (jsn := request.json.get('m2m:rqp')) is None:
		# 	# return Response(response = f'Content is not a request. "m2m:rqp" required', status = 400)
		# 	return prepareUTResponse(RC.badRequest)

		# # TODO Add missing but mandatory attributes (like rqi) - just to keep the dissectMQTTRequest

		# # Dissect and validate
		# if not (dissectResult := dissectMQTTRequest(bytes(json.dumps(jsn), 'utf-8'), ContentSerializationType.JSON.toSimple())).status:
		# 	return prepareUTResponse(RC.badRequest)
		# L.logWarn(dissectResult)

		# result = performBatchOperation(dissectResult)

		# Send the request
		# if dissectResult.request.op == Operation.CREATE:
		# 	result = CSE.request.sendCreateRequest(	uri = dissectResult.request.to, 
		# 											originator = dissectResult.request.headers.originator, 
		# 											ct = dissectResult.request.ct,
		# 											data = dissectResult.request.originalRequest,
		# 											raw = True)
		# elif dissectResult.request.op == Operation.RETRIEVE:
		# 	result = CSE.request.sendRetrieveRequest(	uri = dissectResult.request.to, 
		# 												originator = dissectResult.request.headers.originator, 
		# 												ct = dissectResult.request.ct,
		# 												data = dissectResult.request.originalRequest,
		# 												raw = True)
		# elif dissectResult.request.op == Operation.UPDATE:
		# 	result = CSE.request.sendUpdateRequest(	uri = dissectResult.request.to, 
		# 											originator = dissectResult.request.headers.originator, 
		# 											ct = dissectResult.request.ct,
		# 											data = dissectResult.request.originalRequest,
		# 											raw = True)
		# elif dissectResult.request.op == Operation.DELETE:
		# 	result = CSE.request.sendDeleteRequest(	uri = dissectResult.request.to, 
		# 											originator = dissectResult.request.headers.originator, 
		# 											ct = dissectResult.request.ct,
		# 											data = dissectResult.request.originalRequest,
		# 											raw = True)
		# elif dissectResult.request.op == Operation.NOTIFY:
		# 	result = CSE.request.sendNotifyRequest(	uri = dissectResult.request.to, 
		# 											originator = dissectResult.request.headers.originator, 
		# 											ct = dissectResult.request.ct,
		# 											data = dissectResult.request.originalRequest,
		# 											raw = True)
		# else:
		# 	result = Result(status = False, rsc = RC.badRequest, dbg = f'Unknown operation')
		# 	return prepareUTResponse(RC.badRequest)

		# return prepareUTResponse(RC.OK)
	


	#########################################################################

	#
	#	Send HTTP requests
	#

	operation2method = {
		Operation.CREATE	: requests.post,
		Operation.RETRIEVE	: requests.get,
		Operation.UPDATE 	: requests.put,
		Operation.DELETE 	: requests.delete,
		Operation.NOTIFY 	: requests.post
	}

	def _prepContent(self, content:bytes|str|Any, ct:ContentSerializationType) -> str:
		if not content:	return ''
		if isinstance(content, str): return content
		return content.decode('utf-8') if ct == ContentSerializationType.JSON else TextTools.toHex(content)


	def sendHttpRequest(self, operation:Operation, url:str, originator:str, ty:T=None, data:Any=None, parameters:Parameters=None, ct:ContentSerializationType=None, targetResource:Resource=None, targetOriginator:str=None, raw:bool=False, id:str=None) -> Result:	 # type: ignore[type-arg]
		"""	Send an http request.
		
			The result is returned in *Result.data*.
		"""
		# Set the request method
		method:Callable = self.operation2method[operation]

		# Make the URL a valid http URL (escape // and ///)
		url = RequestUtils.toHttpUrl(url)

		# get the serialization
		ct = CSE.defaultSerialization if not ct else ct

		# Set basic headers
		if not raw:

			hty = f';ty={int(ty):d}' if ty else ''
			hds = {	'User-Agent'	: self.serverID,
					'Content-Type' 	: f'{ct.toHeader()}{hty}',
					'Accept'		: ct.toHeader(),
					'cache-control'	: 'no-cache',
					C.hfOrigin	 	: originator,
					C.hfRI 			: Utils.uniqueRI(),
					C.hfRVI			: CSE.releaseVersion,			# TODO this actually depends in the originator
				}
			# Add additional headers
			if parameters:
				if C.hfcEC in parameters:				# Event Category
					hds[C.hfEC] = parameters[C.hfcEC]

		else:	# raw	-> data contains a whole requests
			# L.logDebug(data)

			hty = f';ty={int(ty):d}' if ty else ''
			hds = {	'User-Agent'	: self.serverID,
					'Content-Type' 	: f'{ct.toHeader()}{hty}',
					'Accept'		: ct.toHeader(),
					C.hfOrigin	 	: Utils.toSPRelative(data['fr']) if 'fr' in data else '',
					C.hfRI 			: data['rqi'],
					C.hfRVI			: data['rvi'],			# TODO this actually depends in the originator
				}
			if 'ec' in data:				# Event Category
				hds[C.hfEC] = data['ec']
			if 'rqet' in data:
				hds[C.hfRET] = data['rqet']
			if 'rset' in data:
				hds[C.hfRST] = data['rset']
			if 'oet' in data:
				hds[C.hfOET] = data['oet']
			if 'rt' in data:
				hds[C.hfRTU] = data['rt']
			if 'vsi' in data:
				hds[C.hfVSI] = data['vsi']
			
			# Add to to URL
			url = f'{url}/~{data["to"]}'
			# re-assign the data to pc
			if 'pc' in data:
				data = data['pc']
		
		# L.logWarn(url)

		# serialize data (only if dictionary, pass on non-dict data)
		content = RequestUtils.serializeData(data, ct) if isinstance(data, dict) else data

		# ! Don't forget: requests are done through the request library, not flask.
		# ! The attribute names are different
		try:
			L.isDebug and L.logDebug(f'Sending request: {method.__name__.upper()} {url}')
			if ct == ContentSerializationType.CBOR:
				L.isDebug and L.logDebug(f'HTTP Request ==>:\nHeaders: {hds}\nBody: \n{self._prepContent(content, ct)}\n=>\n{str(data) if data else ""}\n')
			else:
				L.isDebug and L.logDebug(f'HTTP Request ==>:\nHeaders: {hds}\nBody: \n{self._prepContent(content, ct)}\n')
			
			# Actual sending the request
			r = method(url, data=content, headers=hds, verify=CSE.security.verifyCertificateHttp)

			responseCt = ContentSerializationType.getType(r.headers['Content-Type']) if 'Content-Type' in r.headers else ct
			rc = RC(int(r.headers[C.hfRSC])) if C.hfRSC in r.headers else RC.internalServerError
			L.isDebug and L.logDebug(f'HTTP Response <== ({str(r.status_code)}):\nHeaders: {str(r.headers)}\nBody: \n{self._prepContent(r.content, responseCt)}\n')
		except Exception as e:
			L.isWarn and L.logWarn(f'Failed to send request: {str(e)}')
			return Result(status=False, rsc=RC.targetNotReachable, dbg='target not reachable')
		return Result(status=True, data=RequestUtils.deserializeData(r.content, responseCt), rsc=rc)
		

	#########################################################################

	def _prepareResponse(self, result:Result, originalRequest:CSERequest=None) -> Response:
		"""	Prepare the response for a request. If `request` is given then
			set it for the response.
		"""
		content:str|bytes|JSON = ''
		if not result.request:
			result.request = CSERequest()

		#
		#  Copy a couple of attributes from the originalRequest to the new request
		#

		result.request.ct = CSE.defaultSerialization	# default serialization
		if originalRequest:

			# Determine contentType for the response. Check the 'accept' header first, then take the
			# original request's contentType. If this is not possible, the fallback is still the
			# CSE's default
			result.request.headers.originator = originalRequest.headers.originator
			if originalRequest.headers.accept:																# accept / contentType
				result.request.ct = ContentSerializationType.getType(originalRequest.headers.accept[0])
			elif csz := CSE.request.getSerializationFromOriginator(originalRequest.headers.originator):
				result.request.ct = csz[0]

			result.request.headers.requestIdentifier = originalRequest.headers.requestIdentifier
			result.request.headers.releaseVersionIndicator = originalRequest.headers.releaseVersionIndicator
			result.request.headers.vendorInformation = originalRequest.headers.vendorInformation
	
			# Add additional parameters
			if ec := originalRequest.parameters.get(C.hfEC):												# Event Category, copy from the original request
				result.request.parameters[C.hfEC] = ec
	

		#
		#	Transform request to oneM2M request
		#
		outResult = RequestUtils.requestFromResult(result, isResponse=True)

		#
		#	Transform oneM2M request to http message
		#

		# Build the headers
		headers = {}
		headers['Server'] = self.serverID						# set server field
		if result.rsc:
			headers[C.hfRSC] = f'{int(result.rsc)}'				# set the response status code
		if rqi := Utils.findXPath(cast(JSON, outResult.data), 'rqi'):
			headers[C.hfRI] = rqi
		if rvi := Utils.findXPath(cast(JSON, outResult.data), 'rvi'):
			headers[C.hfRVI] = rvi
		if vsi := Utils.findXPath(cast(JSON, outResult.data), 'vsi'):
			headers[C.hfVSI] = vsi

		# HTTP status code
		statusCode = result.rsc.httpStatusCode()
		
		# Assign and encode content accordingly
		headers['Content-Type'] = (cts := result.request.ct.toHeader())
		# (re-)add an empty pc if it is missing

		# From hereon, data is a string or byte string
		origData:JSON = cast(JSON, outResult.data)
		outResult.data = RequestUtils.serializeData(cast(JSON, outResult.data)['pc'], result.request.ct) if 'pc' in cast(JSON, outResult.data) else ''
		
		# Build and return the response
		if isinstance(outResult.data, bytes):
			L.isDebug and L.logDebug(f'<== HTTP Response ({result.rsc}):\nHeaders: {str(headers)}\nBody: \n{TextTools.toHex(outResult.data)}\n=>\n{str(result.toData())}')
		elif 'pc' in origData:
			# L.isDebug and L.logDebug(f'<== HTTP Response (RSC: {int(result.rsc)}):\nHeaders: {str(headers)}\nBody: {str(content)}\n')
			L.isDebug and L.logDebug(f'<== HTTP Response ({result.rsc}):\nHeaders: {str(headers)}\nBody: {origData["pc"]}\n')	# might be different serialization
		else:
			L.isDebug and L.logDebug(f'<== HTTP Response ({result.rsc}):\nHeaders: {str(headers)}\n')
		return Response(response = outResult.data, status = statusCode, content_type = cts, headers = headers)


	#########################################################################
	#
	#	HTTP request helper functions
	#

	def _dissectHttpRequest(self, request:Request, operation:Operation, path:str) -> Result:
		"""	Dissect an HTTP request. Combine headers and contents into a single structure. Result is returned in Result.request.
		"""

		def extractMultipleArgs(args:MultiDict, argName:str) -> None:
			"""	Get multi-arguments. Remove the found arguments from the original list, but add the new list again with the argument name.
			"""
			lst = [ t for sublist in args.getlist(argName) for t in sublist.split() ]
			args.poplist(argName)	# type: ignore [no-untyped-call] # perhaps even multiple times
			if len(lst) > 0:
				args[argName] = lst


		# def requestHeaderField(request:Request, field:str) -> str:
		# 	"""	Return the value of a specific Request header, or `None` if not found.
		# 	""" 
		# 	return request.headers.get(field)

		# resolve http's /~ and /_ special prefixs
		if path[0] == '~':
			path = path[1:]			# ~/xxx -> /xxx
		elif path[0] == '_':
			path = f'/{path[1:]}'	# _/xxx -> //xxx

		cseRequest 					= CSERequest()
		req:ReqResp 				= {}
		cseRequest.originalData 	= request.data			# get the data first. This marks the request as consumed, just in case that we have to return early
		cseRequest.op 				= operation
		req['op']   				= operation.value		# Needed later for validation
		req['to'] 		 			= path

		# Get the request date
		if date := request.date:
			# req['ot'] = DateUtils.toISO8601Date(DateUtils.utcTime())
			req['ot'] = DateUtils.toISO8601Date(date)
		# else:
		# 	req['ot'] = DateUtils.getResourceDate()

		# Copy and parse the original request headers
		if f := request.headers.get(C.hfOrigin):
			req['fr'] = f
		if f := request.headers.get(C.hfRI):
			req['rqi'] = f
		if f := request.headers.get(C.hfRET):
			req['rqet'] = f
		if f := request.headers.get(C.hfRST):
			req['rset'] = f
		if f := request.headers.get(C.hfOET):
			req['oet'] = f
		if f := request.headers.get(C.hfRVI):
			req['rvi'] = f
		if (rtu := request.headers.get(C.hfRTU)) is not None:	# handle rtu as a list AND it may be an empty list!
			rt = dict()
			rt['nu'] = rtu.split('&')		
			req['rt'] = rt					# req.rt.rtu
		if f := request.headers.get(C.hfVSI):
			req['vsi'] = f

		# parse and extract content-type header
		if ct := request.content_type:
			if not ct.startswith(C.supportedContentHeaderFormatTuple):
				ct = None
			else:
				p  = ct.partition(';')		# always returns a 3-tuple
				ct = p[0] 					# only the content-type without the resource type
				t  = p[2].partition('=')[2]
				if len(t) > 0:
					req['ty'] = t			# Here we found the type for CREATE requests
		cseRequest.headers.contentType = ct

		# parse accept header
		cseRequest.headers.accept 	= [ a for a in request.headers.getlist('accept') if a != '*/*' ]
		cseRequest.originalHttpArgs	= deepcopy(request.args)	# Keep the original args

		# copy request arguments for greedy attributes checking
		args = request.args.copy() 	# type: ignore [no-untyped-call]
		
		# Do some special handling for those arguments that could occur multiple
		# times in the args MultiDict. They are collected together in a single list
		# and added again to args.
		extractMultipleArgs(args, 'ty')	# conversation to int happens later in fillAndValidateCSERequest()
		extractMultipleArgs(args, 'cty')
		extractMultipleArgs(args, 'lbl')

		# Handle some parameters differently.
		# They are not filter criteria, but request attributes
		for param in ['rcn', 'rp', 'drt']:
			if p := args.get(param):	# type: ignore [assignment]
				req[param] = p
				del args[param]
		if rtv := args.get('rt'):
			if not (rt := cast(JSON, req.get('rt'))):
				rt = {}
			rt['rtv'] = rtv		# type: ignore [assignment] # req.rt.rtv
			req['rt'] = rt
			del args['rt']


		# Extract further request arguments from the http request
		# add all the args to the filterCriteria
		# for k,v in args.items():
		# 	filterCriteria[k] = v
		filterCriteria:ReqResp = { k:v for k,v in args.items() }
		if len(filterCriteria) > 0:
			req['fc'] = filterCriteria

		# De-Serialize the content
		if not (contentResult := CSE.request.deserializeContent(cseRequest.originalData, cseRequest.headers.contentType)).status:
			return Result(rsc=contentResult.rsc, request=cseRequest, dbg=contentResult.dbg, status=False)
		
		# Remove 'None' fields *before* adding the pc, because the pc may contain 'None' fields that need to be preserved
		req = Utils.removeNoneValuesFromDict(req)

		# Add the primitive content and 
		req['pc'] 	 				= cast(Tuple, contentResult.data)[0]	# The actual content
		cseRequest.ct				= cast(Tuple, contentResult.data)[1]	# The conten serialization type
		cseRequest.originalRequest	= req									# finally store the oneM2M request object in the cseRequest
		
		# do validation and copying of attributes of the whole request
		try:
			# L.logWarn(str(cseRequest))
			if not (res := CSE.request.fillAndValidateCSERequest(cseRequest)).status:
				return res
		except Exception as e:
			return Result(rsc=RC.badRequest, request=cseRequest, dbg=f'invalid arguments/attributes ({str(e)})', status=False)

		# Here, if everything went okay so far, we have a request to the CSE
		return Result(request=cseRequest, status=True)


	def _hasContentType(self) -> bool:
		return (ct := request.content_type) is not None and any(s.startswith('ty=') for s in ct.split(';'))


##########################################################################
#
#	Own request handler.
#	Actually only to redirect some logging of the http server.
#	This handler does NOT handle requests.
#

class ACMERequestHandler(WSGIRequestHandler):
	# Just like WSGIRequestHandler, but without "- -"
	def log(self, type, message, *args): # type: ignore
		L.enableBindingsLogging and L.isDebug and L.logDebug(f'HTTP: {message % args}')

	# Just like WSGIRequestHandler, but without "code"
	def log_request(self, code='-', size='-'): 	# type: ignore
		L.enableBindingsLogging and L.isDebug and L.logDebug(f'HTTP: "{self.requestline}" {size} {code}')

	def log_message(self, format, *args): 	# type: ignore
		L.enableBindingsLogging and L.isDebug and L.logDebug(f'HTTP: {format % args}')
	

