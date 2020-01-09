# coding=utf-8
from __future__ import absolute_import
import time
import socket
import re

import octoprint.plugin
import octoprint.util
import traceback
from octoprint.events import Events

class TimesAndLayersPlugin(octoprint.plugin.StartupPlugin,
			octoprint.plugin.EventHandlerPlugin,
			octoprint.plugin.SettingsPlugin):

	_last_updated = 0.0
	_last_message = 0
	_repeat_timer = None
	_etl_format = ""
	_eta_strftime = ""
	_messages = []
	_havelayerinfo = False
	currentLayer,totalLayer = 0,0
	
# functions defined in this file:
#   on_after_startup(self)
#   on_event(self, event, payload)
#      amongst other things, events PRINT_XXXXXX set/cancel a timer to call do_work
#   do_work(self)
#   _sanitize_current_data(self, currentData)
#   _get_next_message(self, currentData)
#   _get_time_from_seconds(self, seconds)
#   _get_host_ip(self)
#   get_settings_defaults(self)
#   get_update_information(self)
#   __plugin_load__()
	
	def on_after_startup(self):
		self._logger.info("TimesAndLayers plugin startup.")
		# with thanks to OllisGit for providing this code which tests for his plugin:
		if "DisplayLayerProgress" in self._plugin_manager.plugins:
			plugin = self._plugin_manager.plugins["DisplayLayerProgress"]
			if plugin != None and plugin.enabled == True:
				self._displayLayerProgressPluginImplementation = plugin.implementation
				_havelayerinfo = True
				self._logger.info("TimesAndLayers plugin: can get layer information")
			else:
				self._displayLayerProgressPluginImplementationState = "disabled"
				self._logger.info("TimesAndLayers plugin: will NOT get layer information")
		else:
			self._displayLayerProgressPluginImplementationState = "missing"	
			self._logger.info("TimesAndLayers plugin: can NOT get layer information")

	def on_event(self, event, payload):
		if event == Events.PRINT_STARTED:
			self._logger.info("Printing started, Times And Layers plugin started; event {}".format(event))
			self._etl_format = self._settings.get(["etl_format"])
			self._eta_strftime = self._settings.get(["eta_strftime"])
			self._messages = self._settings.get(["messages"])
			self._repeat_timer = octoprint.util.RepeatedTimer(self._settings.get_int(["time_to_change"]), self.do_work)
			self._repeat_timer.start()
		elif event in (Events.PRINT_DONE, Events.PRINT_FAILED, Events.PRINT_CANCELLED):
			if self._repeat_timer != None:
				self._repeat_timer.cancel()
				self._repeat_timer = None
			if event == Events.PRINT_DONE:
				endreason = "finished"
			elif event == Events.PRINT_FAILED:
				# differentiate "cancelled" and "error"
				if payload["reason"] == "cancelled":
					return
				endreason = "failed"
			elif event == Events.PRINT_CANCELLED:
				endreason = "cancelled"
			self._logger.info("Printing {}. TimesAndLayers plugin stopped.".format(endreason))
			self._printer.commands("M117 Print {}".format(endreason))
		elif event == Events.CONNECTED: 
			myhostname = socket.gethostname()
			ip = self._get_host_ip()
			if not ip:
				self._printer.commands("M117 {} connected".format(myhostname))
				return
			self._printer.commands("M117 {} at {}".format(myhostname, ip))
		elif event == Events.DisplayLayerProgress_layerChanged:
			currentLayer = payload.currentLayer
			totalLayer = payload.totalLayer

	def do_work(self):
		if not self._printer.is_printing():
			#we have nothing to do here
			return
		try:
			currentData = self._printer.get_current_data()
			currentData = self._sanitize_current_data(currentData)

			message = self._get_next_message(currentData)
			self._printer.commands("M117 {}".format(message))
		except Exception as e:
			self._logger.info("Caught an exception {0}\nTraceback:{1}".format(e,traceback.format_exc()))

	def _sanitize_current_data(self, currentData):
		if (currentData["progress"]["printTimeLeft"] == None):
			currentData["progress"]["printTimeLeft"] = currentData["job"]["estimatedPrintTime"]
		# these two values are never used in this plugin
		# if (currentData["progress"]["filepos"] == None):
		#	currentData["progress"]["filepos"] = 0
		#if (currentData["progress"]["printTime"] == None):
		#	and this would be wrong anyway! (unless elapsed time = "None" means it's finished)
		#	currentData["progress"]["printTime"] = currentData["job"]["estimatedPrintTime"]

		currentData["progress"]["printTimeLeftString"] = "No ETL yet"
		currentData["progress"]["ETA"] = "No ETA yet"
		accuracy = currentData["progress"]["printTimeLeftOrigin"]
		if accuracy:
			if accuracy == "estimate":
				accuracy = "best estimate"
			elif accuracy == "average" or accuracy == "genius":
				accuracy = "good"
			elif accuracy == "analysis" or accuracy.startswith("mixed"):
				accuracy = "medium"
			elif accuracy == "linear":
				accuracy = "poor"
			else:
				accuracy = "ERR"
				self._logger.debug("Caught unmapped accuracy value: {0}".format(accuracy))
		else:
			accuracy = "N/A"
		currentData["progress"]["accuracy"] = accuracy

		#Add additional data
		try:
			currentData["progress"]["printTimeLeftString"] = self._get_time_from_seconds(currentData["progress"]["printTimeLeft"])
			currentData["progress"]["ETA"] = time.strftime(self._eta_strftime, time.localtime(time.time() + currentData["progress"]["printTimeLeft"]))
		except Exception as e:
			self._logger.debug("Caught an exception trying to parse data: {0}\n Error is: {1}\nTraceback:{2}".format(currentData,e,traceback.format_exc()))

		return currentData

	def _get_next_message(self, currentData):
		message = self._messages[self._last_message]
		self._last_message += 1
		# if we don't have access to Layer information, don't try to use it
		if (re.search("Layer", message) and not _havelayerinfo):
			self._last_message += 1
		self._last_message = self._last_message % len(self._messages)
		return message.format(
			completion = currentData["progress"]["completion"],
			printTimeLeft = currentData["progress"]["printTimeLeftString"],
			ETA = currentData["progress"]["ETA"],
			# filepos = currentData["progress"]["filepos"],
			accuracy = currentData["progress"]["accuracy"],
		)

	def _get_time_from_seconds(self, seconds):
		hours = 0
		minutes = 0
		if seconds >= 3600:
			hours = int(seconds / 3600)
			seconds = seconds % 3600
		if seconds >= 60:
			minutes = int(seconds / 60)
			seconds = seconds % 60
		return self._etl_format.format(**locals())

	def _get_host_ip(self):
		return [l for l in ([ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")][:1], [[(s.connect(('8.8.8.8', 53)), s.getsockname()[0], s.close()) for s in [socket.socket(socket.AF_INET, socket.SOCK_DGRAM)]][0][1]]) if l][0][0]

#	def get_settings_defaults(self):
#		self._logger.debug("TimesAndLayers applying default settings")
#		return dict(
#			messages = [
#				"{completion:.2f}pc complete",
#				"ETL {printTimeLeft}",
#				"ETA {ETA}",
#				"Accuracy: {accuracy}"
#			],
#			eta_strftime = "%H %M %S %dth %b",
#			etl_format = "{hours:02d}:{minutes:02d}:{seconds:02d}",
#			time_to_change = 6
#		)

       # temporary version while testing/developing - the one above is safer
	def get_settings_defaults(self):
		self._logger.debug("TimesAndLayers applying default settings")
		# For eta_strftime:
		#   %H Hours : %M Minutes : %S Seconds : %d dayofmonth (1-31) : %b 3-charmonthname
		#   or use %m monthnumber (1-12), %a 3-charweekdayname

		return dict(
			messages = [
				"{completion:.2f}% complete",
				"ETL {printTimeLeft}",
				"ETA {ETA}",
				"Layer {currentLayer}/{totalLayer}",
				"Accuracy: {accuracy}"
			],
			eta_strftime = "%H:%M:%S %dth %b",
			etl_format = "{hours:02d}:{minutes:02d}:{seconds:02d}",
			time_to_change = 6
		)

	##~~ Softwareupdate hook

	def get_update_information(self):
		return dict(
			timesandlayers=dict(
				displayName="TimesAndLayers Plugin",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="pnt103",
				repo="OctoPrint-TimesAndLayers",
				current=self._plugin_version,

				# update method: pip
				pip="https://github.com/pnt103/OctoPrint-TimesAndLayers/archive/{target_version}.zip"
			)
		)

__plugin_name__ = "Layers And Times Plugin"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = TimesAndLayersPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
	}

