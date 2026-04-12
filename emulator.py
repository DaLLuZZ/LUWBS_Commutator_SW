#!/usr/bin/env python3
"""
SCPI Instrument Emulator - Matrix Switch 4x4
Based on scpi_server.c and scpi-def.c
"""

import asyncio
import logging
import re
import socket
from enum import Enum
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass, field
from collections import deque

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Try to import env.py for configuration
try:
    import env
    ENV_AVAILABLE = True
except ImportError:
    ENV_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("env.py not found, using default configuration")

# ============================================================================
# Configuration
# ============================================================================

SCPI_DEVICE_PORT = 5025
SCPI_CONTROL_PORT = 5026
SCPI_INPUT_BUFFER_LENGTH = 4096  # Increased for character-by-character input
SCPI_ERROR_QUEUE_SIZE = 20

# SCPI Identification
SCPI_IDN1 = "SCPI-EMU"
SCPI_IDN2 = "MatrixSwitch"
SCPI_IDN3 = "0"
SCPI_IDN4 = "1.0.0"

# Matrix dimensions
MAXROW = 4  # Inputs: VP, VN, AP, AN
MAXCOL = 4  # Outputs: A, B, C, D

# Line ending
SCPI_LINE_ENDING = "\r\n"


# ============================================================================
# SCPI Error Definitions
# ============================================================================

class SCPIErrorCode(Enum):
    NO_ERROR = 0
    COMMAND_ERROR = -100
    INVALID_CHARACTER = -101
    SYNTAX_ERROR = -102
    INVALID_SEPARATOR = -103
    DATA_TYPE_ERROR = -104
    PARAMETER_NOT_ALLOWED = -108
    MISSING_PARAMETER = -109
    UNDEFINED_HEADER = -113
    INVALID_SUFFIX = -131
    SUFFIX_NOT_ALLOWED = -138
    INVALID_STRING_DATA = -151
    EXPRESSION_PARSING_ERROR = -170
    EXECUTION_ERROR = -200
    ILLEGAL_PARAMETER_VALUE = -224
    SYSTEM_ERROR = -310
    QUEUE_OVERFLOW = -350
    INPUT_BUFFER_OVERRUN = -363

ERROR_MESSAGES = {
    SCPIErrorCode.NO_ERROR: "No error",
    SCPIErrorCode.COMMAND_ERROR: "Command error",
    SCPIErrorCode.INVALID_CHARACTER: "Invalid character",
    SCPIErrorCode.SYNTAX_ERROR: "Syntax error",
    SCPIErrorCode.INVALID_SEPARATOR: "Invalid separator",
    SCPIErrorCode.DATA_TYPE_ERROR: "Data type error",
    SCPIErrorCode.PARAMETER_NOT_ALLOWED: "Parameter not allowed",
    SCPIErrorCode.MISSING_PARAMETER: "Missing parameter",
    SCPIErrorCode.UNDEFINED_HEADER: "Undefined header",
    SCPIErrorCode.INVALID_SUFFIX: "Invalid suffix",
    SCPIErrorCode.SUFFIX_NOT_ALLOWED: "Suffix not allowed",
    SCPIErrorCode.INVALID_STRING_DATA: "Invalid string data",
    SCPIErrorCode.EXPRESSION_PARSING_ERROR: "Expression error",
    SCPIErrorCode.EXECUTION_ERROR: "Execution error",
    SCPIErrorCode.ILLEGAL_PARAMETER_VALUE: "Illegal parameter value",
    SCPIErrorCode.SYSTEM_ERROR: "System error",
    SCPIErrorCode.QUEUE_OVERFLOW: "Queue overflow",
    SCPIErrorCode.INPUT_BUFFER_OVERRUN: "Input buffer overrun",
}

# ============================================================================
# IEEE 488.2 Status Registers
# ============================================================================

class StatusByte:
    """Status Byte Register (STB)"""
    def __init__(self):
        self._value = 0
    
    @property
    def value(self) -> int:
        return self._value
    
    def set_bit(self, bit: int):
        self._value |= bit
    
    def clear_bit(self, bit: int):
        self._value &= ~bit
    
    def get_bit(self, bit: int) -> bool:
        return bool(self._value & bit)


class StandardEventStatusRegister:
    """Standard Event Status Register (ESR)"""
    # Bits
    OPC = 0x01  # Operation complete
    REQ = 0x02  # Request Control
    QER = 0x04  # Query Error
    DER = 0x08  # Device Dependent Error
    EER = 0x10  # Execution Error
    CER = 0x20  # Command Error
    URQ = 0x40  # User Request
    PON = 0x80  # Power On
    
    def __init__(self):
        self._value = self.PON  # Set PON bit on startup
    
    @property
    def value(self) -> int:
        return self._value
    
    @value.setter
    def value(self, val: int):
        self._value = val
    
    def set_bit(self, bit: int):
        self._value |= bit
    
    def clear_bit(self, bit: int):
        self._value &= ~bit


class StatusRegisterSet:
    """Complete set of IEEE 488.2 status registers"""
    
    # STB bits
    STB_QMA = 0x04  # Message available
    STB_QES = 0x08  # Questionable status
    STB_MAV = 0x10  # Message Available
    STB_ESR = 0x20  # Standard Event Status Register
    STB_SRQ = 0x40  # Service Request
    STB_OPS = 0x80  # Operation Status Flag
    
    def __init__(self):
        self.stb = StatusByte()
        self.sre = 0  # Service Request Enable
        self.esr = StandardEventStatusRegister()
        self.ese = 0  # Event Status Enable
        self.ques = 0  # Questionable Status Event
        self.quese = 0  # Questionable Status Enable
        self.oper = 0  # Operation Status Event
        self.opere = 0  # Operation Status Enable
        
        # Update STB based on other registers
        self._update_stb()
    
    def _update_stb(self):
        """Update STB based on summary bits from other registers"""
        # ESR summary bit
        if self.esr.value & self.ese:
            self.stb.set_bit(self.STB_ESR)
        else:
            self.stb.clear_bit(self.STB_ESR)
        
        # QES summary bit
        if self.ques & self.quese:
            self.stb.set_bit(self.STB_QES)
        else:
            self.stb.clear_bit(self.STB_QES)
        
        # OPS summary bit
        if self.oper & self.opere:
            self.stb.set_bit(self.STB_OPS)
        else:
            self.stb.clear_bit(self.STB_OPS)
        
        # SRQ generation
        if self.stb.value & self.sre & 0x7F:
            self.stb.set_bit(self.STB_SRQ)
        else:
            self.stb.clear_bit(self.STB_SRQ)
    
    def set_esr_bit(self, bit: int):
        self.esr.set_bit(bit)
        self._update_stb()


# ============================================================================
# Error Queue
# ============================================================================

@dataclass
class ErrorEntry:
    code: int
    message: str
    device_info: Optional[str] = None


class ErrorQueue:
    def __init__(self, size: int = SCPI_ERROR_QUEUE_SIZE):
        self._queue: deque[ErrorEntry] = deque(maxlen=size)
        self._overflow = False
    
    def push(self, code: SCPIErrorCode, device_info: Optional[str] = None) -> None:
        if len(self._queue) >= self._queue.maxlen:
            self._overflow = True
            return
        
        entry = ErrorEntry(
            code=code.value,
            message=ERROR_MESSAGES.get(code, "Unknown error"),
            device_info=device_info
        )
        self._queue.append(entry)
        logger.debug(f"Error pushed: {entry.code} - {entry.message}")
    
    def pop(self) -> Optional[ErrorEntry]:
        if self._queue:
            return self._queue.popleft()
        elif self._overflow:
            self._overflow = False
            return ErrorEntry(
                code=SCPIErrorCode.QUEUE_OVERFLOW.value,
                message=ERROR_MESSAGES[SCPIErrorCode.QUEUE_OVERFLOW]
            )
        return None
    
    def count(self) -> int:
        return len(self._queue)
    
    def clear(self) -> None:
        self._queue.clear()
        self._overflow = False


# ============================================================================
# Matrix Switch State
# ============================================================================

class RouteState(Enum):
    OPENED = 0
    CLOSED = 1


class MatrixSwitch:
    """4x4 Matrix Switch"""
    
    # Row indices (Inputs)
    ROW_VP = 0  # Voltage Positive
    ROW_VN = 1  # Voltage Negative
    ROW_AP = 2  # Amperage Positive
    ROW_AN = 3  # Amperage Negative
    
    # Column indices (Outputs)
    COL_A = 0
    COL_B = 1
    COL_C = 2
    COL_D = 3
    
    ROW_NAMES = ["VP", "VN", "AP", "AN"]
    COL_NAMES = ["A", "B", "C", "D"]
    
    def __init__(self):
        self.routes = [[RouteState.OPENED for _ in range(MAXCOL)] for _ in range(MAXROW)]
        self._delay_ms = 5  # Relay switching delay
    
    def get_state(self, row: int, col: int) -> RouteState:
        if 0 <= row < MAXROW and 0 <= col < MAXCOL:
            return self.routes[row][col]
        raise ValueError(f"Invalid route: {row}!{col}")
    
    def is_closed(self, row: int, col: int) -> bool:
        return self.get_state(row, col) == RouteState.CLOSED
    
    def close(self, row: int, col: int) -> None:
        if 0 <= row < MAXROW and 0 <= col < MAXCOL:
            self.routes[row][col] = RouteState.CLOSED
            logger.info(f"Route closed: {self.ROW_NAMES[row]} -> {self.COL_NAMES[col]} ({row}!{col})")
    
    def open(self, row: int, col: int) -> None:
        if 0 <= row < MAXROW and 0 <= col < MAXCOL:
            self.routes[row][col] = RouteState.OPENED
            logger.info(f"Route opened: {self.ROW_NAMES[row]} -> {self.COL_NAMES[col]} ({row}!{col})")
    
    def open_all(self) -> None:
        for row in range(MAXROW):
            for col in range(MAXCOL):
                self.routes[row][col] = RouteState.OPENED
        logger.info("All routes opened")
    
    def validate_configuration(self, proposed_routes: List[List[RouteState]]) -> Tuple[bool, Optional[str]]:
        """
        Validate that no two inputs are connected to the same output,
        and no two outputs are connected to the same input.
        """
        # Check columns (outputs) - no two inputs on same output
        for col in range(MAXCOL):
            closed_count = sum(1 for row in range(MAXROW) if proposed_routes[row][col] == RouteState.CLOSED)
            if closed_count > 1:
                return False, f"Two or more inputs closed on output {self.COL_NAMES[col]}"
        
        # Check rows (inputs) - no two outputs on same input
        for row in range(MAXROW):
            closed_count = sum(1 for col in range(MAXCOL) if proposed_routes[row][col] == RouteState.CLOSED)
            if closed_count > 1:
                return False, f"Two or more outputs closed on input {self.ROW_NAMES[row]}"
        
        return True, None
    
    def get_delay_ms(self) -> int:
        return self._delay_ms
    
    def get_all_closed_routes(self) -> List[Tuple[int, int]]:
        """Return list of all closed routes as (row, col) tuples"""
        closed = []
        for row in range(MAXROW):
            for col in range(MAXCOL):
                if self.routes[row][col] == RouteState.CLOSED:
                    closed.append((row, col))
        return closed


# ============================================================================
# Channel List Parser
# ============================================================================

class ChannelListParser:
    """Parser for SCPI channel list expressions like (@1!2,3!4:5!6)"""
    
    @staticmethod
    def parse(channel_list: str) -> List[Tuple[int, int]]:
        """
        Parse channel list expression and return list of (row, col) tuples.
        Format: (@1!2,3!4:5!6) - 1-based indexing as per SCPI-99
        Also handles 0-based indexing for compatibility
        """
        result = []
        
        # Remove outer parentheses and @
        expr = channel_list.strip()
        if expr.startswith("(@"):
            expr = expr[2:]
        elif expr.startswith("("):
            expr = expr[1:]
        if expr.endswith(")"):
            expr = expr[:-1]
        
        if not expr:
            return result
        
        # Split by comma for multiple entries
        parts = expr.split(",")
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                # Range: from!to:from!to
                result.extend(ChannelListParser._parse_range(part))
            else:
                # Single channel
                result.append(ChannelListParser._parse_single(part))
        
        return result
    
    @staticmethod
    def _parse_single(spec: str) -> Tuple[int, int]:
        """Parse single channel like '1!2' or '1' (1-based or 0-based indexing)"""
        if "!" in spec:
            parts = spec.split("!")
            row = int(parts[0])
            col = int(parts[1])
            # Keep as-is, normalization will happen later
        else:
            row = int(spec)
            col = 0
        return (row, col)
    
    @staticmethod
    def _parse_range(spec: str) -> List[Tuple[int, int]]:
        """Parse range like '1!2:3!4' (1-based or 0-based indexing)"""
        result = []
        start_str, end_str = spec.split(":")
        
        if "!" in start_str and "!" in end_str:
            start_row, start_col = map(int, start_str.split("!"))
            end_row, end_col = map(int, end_str.split("!"))
            
            # Determine direction
            row_step = 1 if start_row <= end_row else -1
            col_step = 1 if start_col <= end_col else -1
            
            row = start_row
            while True:
                col = start_col
                while True:
                    result.append((row, col))
                    if col == end_col:
                        break
                    col += col_step
                if row == end_row:
                    break
                row += row_step
        else:
            # Single dimension range like '1:3'
            start = int(start_str)
            end = int(end_str)
            step = 1 if start <= end else -1
            
            val = start
            while True:
                result.append((val, 0))
                if val == end:
                    break
                val += step
        
        return result


# ============================================================================
# SCPI Parser and Command Handlers
# ============================================================================

class SCPICommandHandler:
    """Base class for SCPI command handlers"""
    
    def __init__(self, instrument: 'SCPIInstrument'):
        self.instrument = instrument
        self._command_handled = False
    
    def handle(self, command: str) -> Optional[str]:
        """Handle a command, return response or None"""
        raise NotImplementedError


class IEEE488Commands(SCPICommandHandler):
    """IEEE 488.2 Common Commands"""
    
    def handle(self, command: str) -> Optional[str]:
        cmd_upper = command.upper()
        self._command_handled = False
        
        if cmd_upper == "*CLS":
            self._command_handled = True
            return self._cls()
        elif cmd_upper == "*ESE":
            self._command_handled = True
            return None
        elif cmd_upper == "*ESE?":
            self._command_handled = True
            return self._ese_q()
        elif cmd_upper == "*ESR?":
            self._command_handled = True
            return self._esr_q()
        elif cmd_upper == "*IDN?":
            self._command_handled = True
            return self._idn_q()
        elif cmd_upper == "*OPC":
            self._command_handled = True
            return self._opc()
        elif cmd_upper == "*OPC?":
            self._command_handled = True
            return self._opc_q()
        elif cmd_upper == "*RST":
            self._command_handled = True
            return self._rst()
        elif cmd_upper == "*SRE":
            self._command_handled = True
            return None
        elif cmd_upper == "*SRE?":
            self._command_handled = True
            return self._sre_q()
        elif cmd_upper == "*STB?":
            self._command_handled = True
            return self._stb_q()
        elif cmd_upper == "*TST?":
            self._command_handled = True
            return self._tst_q()
        elif cmd_upper == "*WAI":
            self._command_handled = True
            return None
        
        return None
    
    def _cls(self) -> None:
        self.instrument.error_queue.clear()
        self.instrument.registers.esr.value = 0
        self.instrument.registers.ques = 0
        self.instrument.registers.oper = 0
        self.instrument.registers._update_stb()
        return None
    
    def _ese_q(self) -> str:
        return str(self.instrument.registers.ese)
    
    def _esr_q(self) -> str:
        value = self.instrument.registers.esr.value
        self.instrument.registers.esr.value = 0
        self.instrument.registers._update_stb()
        return str(value)
    
    def _idn_q(self) -> str:
        return f"{SCPI_IDN1},{SCPI_IDN2},{SCPI_IDN3},{SCPI_IDN4}"
    
    def _opc(self) -> None:
        self.instrument.registers.set_esr_bit(StandardEventStatusRegister.OPC)
        return None
    
    def _opc_q(self) -> str:
        return "1"
    
    def _rst(self) -> None:
        self.instrument.matrix.open_all()
        logger.info("**Reset: opened all routes")
        return None
    
    def _sre_q(self) -> str:
        return str(self.instrument.registers.sre)
    
    def _stb_q(self) -> str:
        return str(self.instrument.registers.stb.value)
    
    def _tst_q(self) -> str:
        return "0"


class SystemCommands(SCPICommandHandler):
    """SYSTem commands"""
    
    # All possible variants for commands
    ERROR_NEXT_COMMANDS = [
        "SYST:ERR?",
        "SYST:ERR:NEXT?",
        "SYSTEM:ERROR?",
        "SYSTEM:ERROR:NEXT?",
    ]
    
    ERROR_COUNT_COMMANDS = [
        "SYST:ERR:COUN?",
        "SYSTEM:ERROR:COUNT?",
    ]
    
    VERSION_COMMANDS = [
        "SYST:VERS?",
        "SYSTEM:VERSION?",
    ]
    
    CONTROL_PORT_COMMANDS = [
        "SYST:COMM:TCPIP:CONTROL?",
        "SYSTEM:COMMUNICATION:TCPIP:CONTROL?",
    ]
    
    def handle(self, command: str) -> Optional[str]:
        cmd_upper = command.upper()
        self._command_handled = False
        
        if cmd_upper in self.ERROR_NEXT_COMMANDS:
            self._command_handled = True
            return self._error_next()
        elif cmd_upper in self.ERROR_COUNT_COMMANDS:
            self._command_handled = True
            return self._error_count()
        elif cmd_upper in self.VERSION_COMMANDS:
            self._command_handled = True
            return self._version()
        elif cmd_upper in self.CONTROL_PORT_COMMANDS:
            self._command_handled = True
            return str(SCPI_CONTROL_PORT)
        
        return None
    
    def _error_next(self) -> str:
        error = self.instrument.error_queue.pop()
        if error:
            if error.device_info:
                return f'{error.code},"{error.message};{error.device_info}"'
            else:
                return f'{error.code},"{error.message}"'
        return '0,"No error"'
    
    def _error_count(self) -> str:
        return str(self.instrument.error_queue.count())
    
    def _version(self) -> str:
        return "1999.0"


class StatusCommands(SCPICommandHandler):
    """STATus commands"""
    
    QUES_EVENT_COMMANDS = [
        "STAT:QUES?",
        "STAT:QUES:EVEN?",
        "STATUS:QUESTIONABLE?",
        "STATUS:QUESTIONABLE:EVENT?",
    ]
    
    QUES_ENABLE_COMMANDS = [
        "STAT:QUES:ENAB",
        "STATUS:QUESTIONABLE:ENABLE",
    ]
    
    QUES_ENABLE_QUERY_COMMANDS = [
        "STAT:QUES:ENAB?",
        "STATUS:QUESTIONABLE:ENABLE?",
    ]
    
    OPER_EVENT_COMMANDS = [
        "STAT:OPER?",
        "STAT:OPER:EVEN?",
        "STATUS:OPERATION?",
        "STATUS:OPERATION:EVENT?",
    ]
    
    OPER_ENABLE_COMMANDS = [
        "STAT:OPER:ENAB",
        "STATUS:OPERATION:ENABLE",
    ]
    
    OPER_ENABLE_QUERY_COMMANDS = [
        "STAT:OPER:ENAB?",
        "STATUS:OPERATION:ENABLE?",
    ]
    
    PRESET_COMMANDS = [
        "STAT:PRES",
        "STATUS:PRESET",
    ]
    
    def handle(self, command: str) -> Optional[str]:
        cmd_upper = command.upper()
        self._command_handled = False
        
        if cmd_upper in self.QUES_EVENT_COMMANDS:
            self._command_handled = True
            return self._ques_event()
        elif cmd_upper in self.QUES_ENABLE_COMMANDS:
            self._command_handled = True
            return None
        elif cmd_upper in self.QUES_ENABLE_QUERY_COMMANDS:
            self._command_handled = True
            return self._quese_q()
        elif cmd_upper in self.OPER_EVENT_COMMANDS:
            self._command_handled = True
            return self._oper_event()
        elif cmd_upper in self.OPER_ENABLE_COMMANDS:
            self._command_handled = True
            return None
        elif cmd_upper in self.OPER_ENABLE_QUERY_COMMANDS:
            self._command_handled = True
            return self._opere_q()
        elif cmd_upper in self.PRESET_COMMANDS:
            self._command_handled = True
            return self._preset()
        
        return None
    
    def _ques_event(self) -> str:
        value = self.instrument.registers.ques
        self.instrument.registers.ques = 0
        self.instrument.registers._update_stb()
        return str(value)
    
    def _quese_q(self) -> str:
        return str(self.instrument.registers.quese)
    
    def _oper_event(self) -> str:
        value = self.instrument.registers.oper
        self.instrument.registers.oper = 0
        self.instrument.registers._update_stb()
        return str(value)
    
    def _opere_q(self) -> str:
        return str(self.instrument.registers.opere)
    
    def _preset(self) -> None:
        self.instrument.registers.ques = 0
        self.instrument.registers._update_stb()
        return None


class RouteCommands(SCPICommandHandler):
    """ROUTe commands"""
    
    # Short form mappings (SCPI allows abbreviations)
    COMMAND_PATTERNS = {
        "ROUT:CLOS": "CLOSE",
        "ROUTE:CLOSE": "CLOSE",
        "ROUT:OPEN": "OPEN",
        "ROUTE:OPEN": "OPEN",
    }
    
    # All possible variants for OPEN:ALL
    OPEN_ALL_COMMANDS = [
        "ROUT:OPEN:ALL",
        "ROUTE:OPEN:ALL",
        "ROUT:OPEN:ALL",
        "ROUTE:OPEN:ALL",
    ]
    
    # All possible variants for CLOS:STAT?
    CLOSE_STATE_COMMANDS = [
        "ROUT:CLOS:STAT",
        "ROUTE:CLOSE:STAT",
        "ROUT:CLOS:STAT",
        "ROUTE:CLOS:STAT",
        "ROUT:CLOSE:STAT",
    ]
    
    def handle(self, command: str) -> Optional[str]:
        self._command_handled = False
        
        # Split command and parameters by first whitespace
        parts = command.split(None, 1)
        cmd_only = parts[0].upper() if parts else ""
        params = parts[1] if len(parts) > 1 else ""
        
        # Check if it's a query
        is_query = cmd_only.endswith("?")
        base_cmd = cmd_only[:-1] if is_query else cmd_only
        
        # Check for ROUT:CLOS:STAT? or variants
        if base_cmd in self.CLOSE_STATE_COMMANDS:
            self._command_handled = True
            return self._close_state_query()
        
        # Check for ROUT:OPEN:ALL or variants
        if base_cmd in self.OPEN_ALL_COMMANDS:
            self._command_handled = True
            return self._open_all()
        
        # Check patterns for CLOSE/OPEN
        for pattern, action in self.COMMAND_PATTERNS.items():
            if base_cmd.startswith(pattern):
                self._command_handled = True
                if action == "CLOSE":
                    if is_query:
                        return self._close_query(params)
                    else:
                        return self._close(params)
                elif action == "OPEN":
                    if is_query:
                        return self._open_query(params)
                    else:
                        return self._open(params)
        
        return None
    
    def _normalize_routes(self, routes: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """
        Normalize route indices.
        If indices are >= MAX, assume 1-based and subtract 1.
        """
        normalized = []
        for row, col in routes:
            # If index is >= MAX, it's likely 1-based
            if row >= MAXROW:
                row = row - 1
            if col >= MAXCOL:
                col = col - 1
            normalized.append((row, col))
        return normalized
    
    def _close(self, channel_list: str) -> Optional[str]:
        """ROUTe:CLOSe <channel_list>"""
        if not channel_list:
            self._push_error(SCPIErrorCode.MISSING_PARAMETER)
            return None
        
        try:
            routes = ChannelListParser.parse(channel_list)
        except Exception as e:
            self._push_error(SCPIErrorCode.EXPRESSION_PARSING_ERROR, str(e))
            return None
        
        # Normalize routes
        valid_routes = self._normalize_routes(routes)
        
        # Validate all routes are within bounds
        for row, col in valid_routes:
            if not (0 <= row < MAXROW and 0 <= col < MAXCOL):
                self._push_error(SCPIErrorCode.ILLEGAL_PARAMETER_VALUE, f"Invalid route: {row}!{col}")
                return None
        
        # Create proposed configuration
        proposed = [row[:] for row in self.instrument.matrix.routes]
        for row, col in valid_routes:
            proposed[row][col] = RouteState.CLOSED
        
        # Validate
        valid, error_msg = self.instrument.matrix.validate_configuration(proposed)
        if not valid:
            self._push_error(SCPIErrorCode.EXECUTION_ERROR, error_msg)
            return None
        
        # Apply
        for row, col in valid_routes:
            self.instrument.matrix.close(row, col)
        
        logger.info(f"ROUT:CLOS executed, {len(valid_routes)} routes")
        return None
    
    def _open(self, channel_list: str) -> Optional[str]:
        """ROUTe:OPEN <channel_list>"""
        if not channel_list:
            self._push_error(SCPIErrorCode.MISSING_PARAMETER)
            return None
        
        try:
            routes = ChannelListParser.parse(channel_list)
        except Exception as e:
            self._push_error(SCPIErrorCode.EXPRESSION_PARSING_ERROR, str(e))
            return None
        
        # Normalize routes
        valid_routes = self._normalize_routes(routes)
        
        for row, col in valid_routes:
            if 0 <= row < MAXROW and 0 <= col < MAXCOL:
                self.instrument.matrix.open(row, col)
            else:
                self._push_error(SCPIErrorCode.ILLEGAL_PARAMETER_VALUE, f"Invalid route: {row}!{col}")
                return None
        
        logger.info(f"ROUT:OPEN executed, {len(valid_routes)} routes")
        return None
    
    def _close_query(self, channel_list: str) -> str:
        """ROUTe:CLOSe? <channel_list>"""
        try:
            routes = ChannelListParser.parse(channel_list)
        except Exception:
            return ""
        
        valid_routes = self._normalize_routes(routes)
        
        result = []
        for row, col in valid_routes:
            if 0 <= row < MAXROW and 0 <= col < MAXCOL:
                result.append("1" if self.instrument.matrix.is_closed(row, col) else "0")
            else:
                result.append("0")
        
        return ",".join(result)
    
    def _close_state_query(self) -> str:
        """ROUTe:CLOSe:STATe? - returns all closed routes as channel list"""
        closed = self.instrument.matrix.get_all_closed_routes()
        if not closed:
            return "(@)"
        
        parts = [f"{row+1}!{col+1}" for row, col in closed]  # Return 1-based
        return f"(@{','.join(parts)})"
    
    def _open_query(self, channel_list: str) -> str:
        """ROUTe:OPEN? <channel_list>"""
        try:
            routes = ChannelListParser.parse(channel_list)
        except Exception:
            return ""
        
        valid_routes = self._normalize_routes(routes)
        
        result = []
        for row, col in valid_routes:
            if 0 <= row < MAXROW and 0 <= col < MAXCOL:
                result.append("1" if not self.instrument.matrix.is_closed(row, col) else "0")
            else:
                result.append("1")
        
        return ",".join(result)
    
    def _open_all(self) -> None:
        self.instrument.matrix.open_all()
        logger.info("ROUT:OPEN:ALL executed")
        return None
    
    def _push_error(self, code: SCPIErrorCode, info: str = None):
        self.instrument.error_queue.push(code, info)
        self.instrument.registers.set_esr_bit(StandardEventStatusRegister.EER)


class TestCommands(SCPICommandHandler):
    """TEST commands (for debugging)"""
    
    def handle(self, command: str) -> Optional[str]:
        cmd_upper = command.upper()
        self._command_handled = False
        
        if cmd_upper.startswith("TEST:BOOL"):
            self._command_handled = True
            return None
        elif cmd_upper == "TEST:CHO?":
            self._command_handled = True
            return "5"
        elif cmd_upper.startswith("TEST:CHAN"):
            self._command_handled = True
            return None
        
        return None


# ============================================================================
# SCPI Instrument Core
# ============================================================================

class SCPIInstrument:
    """Main SCPI instrument emulator"""
    
    def __init__(self):
        self.matrix = MatrixSwitch()
        self.registers = StatusRegisterSet()
        self.error_queue = ErrorQueue()
        
        # Command handlers
        self.handlers: List[SCPICommandHandler] = [
            IEEE488Commands(self),
            SystemCommands(self),
            StatusCommands(self),
            RouteCommands(self),
            TestCommands(self),
        ]
        
        # Input buffer for line assembly
        self._input_buffer = ""
        
        logger.info(f"SCPI Instrument initialized: {SCPI_IDN1} {SCPI_IDN2}")
    
    def process_command(self, command: str) -> Optional[str]:
        """Process a single SCPI command and return response"""
        command = command.strip()
        if not command:
            return None
        
        logger.debug(f"Processing command: {repr(command)}")
        
        # Check if it's a query (ends with ?)
        is_query = command.endswith("?")
        
        # Try all handlers
        for handler in self.handlers:
            try:
                response = handler.handle(command)
                # Check if handler processed the command
                if hasattr(handler, '_command_handled') and handler._command_handled:
                    if is_query and response is not None:
                        self.registers.stb.set_bit(StatusRegisterSet.STB_MAV)
                    if response is not None:
                        logger.debug(f"Response: {repr(response)}")
                    return response
            except Exception as e:
                logger.error(f"Error handling command '{command}': {e}")
                self.error_queue.push(SCPIErrorCode.SYSTEM_ERROR, str(e))
                return None
        
        # Command not recognized
        logger.warning(f"Undefined header: {repr(command)}")
        self.error_queue.push(SCPIErrorCode.UNDEFINED_HEADER, command)
        self.registers.set_esr_bit(StandardEventStatusRegister.CER)
        return None
    
    def _split_commands(self, text: str) -> List[str]:
        """Split commands by semicolon, ignoring semicolons inside quotes"""
        commands = []
        current = []
        in_quotes = False
        quote_char = None
        
        for char in text:
            if char in ('"', "'") and not in_quotes:
                in_quotes = True
                quote_char = char
            elif char == quote_char and in_quotes:
                in_quotes = False
                quote_char = None
            elif char == ';' and not in_quotes:
                commands.append(''.join(current))
                current = []
                continue
            
            current.append(char)
        
        if current:
            commands.append(''.join(current))
        
        return commands
    
    def _process_command_part(self, cmd_part: str) -> List[str]:
        """Process a command part that may contain multiple semicolon-separated commands"""
        responses = []
        
        # Split by semicolon, but respect quoted strings
        commands = self._split_commands(cmd_part)
        
        for cmd in commands:
            cmd = cmd.strip()
            if cmd:
                response = self.process_command(cmd)
                if response is not None:
                    responses.append(response)
        
        return responses
    
    def process_input(self, data: str) -> List[str]:
        """
        Process incoming data, handling multiple commands separated by semicolons or newlines.
        Returns list of responses.
        """
        responses = []
        self._input_buffer += data
        
        # Process complete commands
        while True:
            # Look for line ending (CR, LF, or CRLF)
            cr_pos = self._input_buffer.find('\r')
            lf_pos = self._input_buffer.find('\n')
            
            # Determine the earliest line ending
            newline_pos = -1
            if cr_pos != -1 and lf_pos != -1:
                newline_pos = min(cr_pos, lf_pos)
            elif cr_pos != -1:
                newline_pos = cr_pos
            elif lf_pos != -1:
                newline_pos = lf_pos
            
            # Also look for semicolon (command separator)
            semicolon_pos = self._input_buffer.find(";")
            
            # Determine which comes first
            if newline_pos != -1 and (semicolon_pos == -1 or newline_pos < semicolon_pos):
                # Process up to newline
                cmd_part = self._input_buffer[:newline_pos].strip()
                
                # Skip the line ending characters
                skip = newline_pos + 1
                # Check for CRLF sequence
                if cr_pos != -1 and lf_pos != -1 and abs(cr_pos - lf_pos) == 1:
                    skip = max(cr_pos, lf_pos) + 1
                
                self._input_buffer = self._input_buffer[skip:]
                
                # Process any semicolon-separated commands in this part
                if cmd_part:
                    sub_responses = self._process_command_part(cmd_part)
                    responses.extend(sub_responses)
                
                # After newline, clear MAV
                self.registers.stb.clear_bit(StatusRegisterSet.STB_MAV)
                
            elif semicolon_pos != -1:
                # Process up to semicolon
                cmd_part = self._input_buffer[:semicolon_pos].strip()
                self._input_buffer = self._input_buffer[semicolon_pos + 1:]
                
                if cmd_part:
                    sub_responses = self._process_command_part(cmd_part)
                    responses.extend(sub_responses)
            else:
                # No complete command found
                break
        
        # Prevent buffer overflow
        if len(self._input_buffer) > SCPI_INPUT_BUFFER_LENGTH:
            logger.warning("Input buffer overflow, clearing")
            self.error_queue.push(SCPIErrorCode.INPUT_BUFFER_OVERRUN)
            self._input_buffer = ""
        
        return responses


# ============================================================================
# TCP Server
# ============================================================================

class SCPITCPServer:
    """TCP/IP server for SCPI instrument"""
    
    def __init__(self, instrument: SCPIInstrument, host: str = '0.0.0.0', port: int = SCPI_DEVICE_PORT):
        self.instrument = instrument
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.clients: set[asyncio.StreamWriter] = set()
    
    async def start(self):
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        logger.info(f"SCPI Server listening on {self.host}:{self.port}")
    
    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        
        # Close all client connections
        for writer in self.clients:
            writer.close()
        await asyncio.gather(*[writer.wait_closed() for writer in self.clients], return_exceptions=True)
        self.clients.clear()
    
    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        client_addr = writer.get_extra_info('peername')
        logger.info(f"Connection established from {client_addr}")
        self.clients.add(writer)
        
        try:
            while True:
                try:
                    # Read data with timeout
                    data = await asyncio.wait_for(reader.read(1024), timeout=60.0)
                    if not data:
                        break
                    
                    # Decode and process
                    try:
                        text = data.decode('utf-8', errors='replace')
                    except UnicodeDecodeError:
                        text = data.decode('latin-1', errors='replace')
                    
                    # Log received data (escaped for readability)
                    logger.debug(f"Received: {repr(text)}")
                    
                    # Process commands
                    responses = self.instrument.process_input(text)
                    
                    # Send responses
                    for response in responses:
                        full_response = response + SCPI_LINE_ENDING
                        writer.write(full_response.encode('utf-8'))
                        await writer.drain()
                        logger.debug(f"Sent: {repr(response)}")
                    
                except asyncio.TimeoutError:
                    # Send a no-op to keep connection alive
                    self.instrument.process_input("")
                    continue
                except ConnectionError:
                    break
                    
        except Exception as e:
            logger.error(f"Client handler error: {e}")
        finally:
            logger.info(f"Connection closed from {client_addr}")
            self.clients.discard(writer)
            writer.close()
            await writer.wait_closed()


class SCPIControlServer:
    """TCP/IP server for SCPI control/SRQ channel"""
    
    def __init__(self, instrument: SCPIInstrument, host: str = '0.0.0.0', port: int = SCPI_CONTROL_PORT):
        self.instrument = instrument
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.client: Optional[asyncio.StreamWriter] = None
    
    async def start(self):
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        logger.info(f"SCPI Control Server listening on {self.host}:{self.port}")
    
    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        
        if self.client:
            self.client.close()
            await self.client.wait_closed()
            self.client = None
    
    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        client_addr = writer.get_extra_info('peername')
        logger.info(f"Control connection established from {client_addr}")
        
        # Only allow one control connection
        if self.client:
            logger.warning("Control connection already exists, rejecting")
            writer.close()
            return
        
        self.client = writer
        
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                # Control channel - could implement SRQ notifications here
        except Exception as e:
            logger.error(f"Control handler error: {e}")
        finally:
            logger.info(f"Control connection closed from {client_addr}")
            self.client = None
            writer.close()
            await writer.wait_closed()


# ============================================================================
# CLI Interface for Testing
# ============================================================================

class SCPICLIServer:
    """Command-line interface for local testing"""
    
    def __init__(self, instrument: SCPIInstrument):
        self.instrument = instrument
        self.running = False
    
    async def start(self):
        self.running = True
        logger.info("SCPI CLI started. Type 'quit' to exit.")
        print("\nSCPI Instrument Emulator - CLI Mode")
        print("Enter SCPI commands (e.g., *IDN?, ROUT:CLOS (@1!1))")
        print("Type 'quit' to exit, 'status' to show matrix state\n")
        
        loop = asyncio.get_event_loop()
        
        while self.running:
            try:
                # Use asyncio.to_thread for non-blocking input
                command = await loop.run_in_executor(None, input, "SCPI> ")
                command = command.strip()
                
                if command.lower() == "quit":
                    self.running = False
                    break
                elif command.lower() == "status":
                    self._show_status()
                elif command.lower() == "errors":
                    self._show_errors()
                elif command.lower() == "registers":
                    self._show_registers()
                elif command.lower() == "help":
                    self._show_help()
                elif command:
                    # Process command (may contain semicolons and newlines)
                    responses = self.instrument.process_input(command + "\n")
                    for response in responses:
                        print(response)
                    
                    # Check for errors if no response
                    if not responses:
                        error = self.instrument.error_queue.pop()
                        if error and error.code != 0:
                            print(f"Error: {error.code} - {error.message}")
            except EOFError:
                break
            except Exception as e:
                print(f"Error: {e}")
        
        logger.info("SCPI CLI stopped")
    
    def _show_status(self):
        """Display current matrix state"""
        print("\nMatrix State (4x4):")
        print("     ", end="")
        for col in range(MAXCOL):
            print(f"  {MatrixSwitch.COL_NAMES[col]}  ", end="")
        print()
        print("   +" + "-" * (MAXCOL * 8) + "+")
        
        for row in range(MAXROW):
            print(f"{MatrixSwitch.ROW_NAMES[row]}  |", end="")
            for col in range(MAXCOL):
                state = "CLOSED" if self.instrument.matrix.is_closed(row, col) else "OPEN  "
                print(f" {state} ", end="")
            print("|")
        print("   +" + "-" * (MAXCOL * 8) + "+")
        print()
    
    def _show_errors(self):
        """Display error queue"""
        print("\nError Queue:")
        count = 0
        while True:
            error = self.instrument.error_queue.pop()
            if not error:
                break
            print(f"  {error.code}: {error.message}")
            if error.device_info:
                print(f"       Info: {error.device_info}")
            count += 1
        if count == 0:
            print("  (empty)")
        print()
    
    def _show_registers(self):
        """Display status registers"""
        regs = self.instrument.registers
        print("\nStatus Registers:")
        print(f"  STB:  0x{regs.stb.value:02X} ({regs.stb.value})")
        print(f"  SRE:  0x{regs.sre:02X} ({regs.sre})")
        print(f"  ESR:  0x{regs.esr.value:02X} ({regs.esr.value})")
        print(f"  ESE:  0x{regs.ese:02X} ({regs.ese})")
        print()
    
    def _show_help(self):
        """Show help"""
        print("""
Available Commands:
  IEEE 488.2:
  *IDN?                    - Identification query
  *RST                     - Reset (open all routes)
  *CLS                     - Clear status
  *ESR?                    - Event status register query
  *STB?                    - Status byte query
  *TST?                    - Self-test query
  *OPC                     - Set operation complete
  *OPC?                    - Query operation complete
  
  ROUTe:
  ROUT:CLOS (@1!1,2!2)     - Close specified routes
  ROUT:OPEN (@1!1)         - Open specified routes
  ROUT:OPEN:ALL            - Open all routes
  ROUT:CLOS? (@1!1)        - Query if routes are closed
  ROUT:OPEN? (@1!1)        - Query if routes are open
  ROUT:CLOS:STAT?          - Get all closed routes
  
  SYSTem:
  SYST:ERR?                - Get next error
  SYST:ERR:COUN?           - Get error count
  SYST:VERS?               - Get SCPI version
  
  Local commands:
  status                   - Show matrix state
  errors                   - Show error queue
  registers                - Show status registers
  help                     - Show this help
  quit                     - Exit
""")

# ============================================================================
# Main Entry Point
# ============================================================================

async def main():
    import argparse
    import sys
    
    # Default values (can be overridden by env.py or command line)
    default_host = '0.0.0.0'
    default_port = SCPI_DEVICE_PORT
    default_control_port = SCPI_CONTROL_PORT
    
    # Try to load configuration from env.py
    if ENV_AVAILABLE:
        try:
            if hasattr(env, 'VISA_RN_HOST_IP'):
                default_host = env.VISA_RN_HOST_IP
                logger.info(f"Loaded host from env.py: {default_host}")
            if hasattr(env, 'VISA_RN_HOST_PORT'):
                default_port = int(env.VISA_RN_HOST_PORT)
                logger.info(f"Loaded port from env.py: {default_port}")
            if hasattr(env, 'VISA_RN_INT_TYPE'):
                logger.info(f"Interface type from env.py: {env.VISA_RN_INT_TYPE}")
            if hasattr(env, 'VISA_RN_CLASS'):
                logger.info(f"Connection class from env.py: {env.VISA_RN_CLASS}")
        except Exception as e:
            logger.warning(f"Error loading env.py configuration: {e}")
    
    parser = argparse.ArgumentParser(description='SCPI Instrument Emulator - 4x4 Matrix Switch')
    parser.add_argument('--mode', choices=['tcp', 'cli', 'both'], default='both',
                       help='Run mode: tcp server, cli, or both (default: both)')
    parser.add_argument('--host', type=str, default=default_host,
                       help=f'IP address to bind (default from env.py or {default_host})')
    parser.add_argument('--port', type=int, default=default_port,
                       help=f'TCP port for SCPI server (default from env.py or {SCPI_DEVICE_PORT})')
    parser.add_argument('--control-port', type=int, default=default_control_port,
                       help=f'TCP port for control server (default: {SCPI_CONTROL_PORT})')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--no-env', action='store_true', help='Ignore env.py configuration')
    
    args = parser.parse_args()
    
    # Override with command line if --no-env is specified
    if args.no_env:
        if args.host == default_host and ENV_AVAILABLE:
            args.host = '0.0.0.0'
            logger.info("--no-env specified, using default host: 0.0.0.0")
        if args.port == default_port and ENV_AVAILABLE:
            args.port = SCPI_DEVICE_PORT
            logger.info(f"--no-env specified, using default port: {SCPI_DEVICE_PORT}")
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)
    
    # Create instrument
    instrument = SCPIInstrument()
    
    servers = []
    
    if args.mode in ('tcp', 'both'):
        # Start TCP servers
        tcp_server = SCPITCPServer(instrument, args.host, args.port)
        control_server = SCPIControlServer(instrument, args.host, args.control_port)
        
        await tcp_server.start()
        await control_server.start()
        
        servers.extend([tcp_server, control_server])
        
        print(f"\n{'='*60}")
        print(f"SCPI Server Configuration:")
        print(f"{'='*60}")
        print(f"  Host:         {args.host}")
        print(f"  Device port:  {args.port}")
        print(f"  Control port: {args.control_port}")
        
        if ENV_AVAILABLE and not args.no_env:
            print(f"\n  VISA Resource Name (for PyVISA):")
            print(f"  {env.VISA_RESOURCE_NAME}")
        
        print(f"\n{'='*60}")
        print("Connect using any SCPI client (e.g., telnet, netcat, PyVISA)")
        print("Press Ctrl+C to stop")
        print(f"{'='*60}\n")
    
    if args.mode in ('cli', 'both'):
        # Start CLI
        cli = SCPICLIServer(instrument)
        await cli.start()
    else:
        # Only TCP mode - keep server running
        try:
            # Wait forever
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            for server in servers:
                await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
