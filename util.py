import env
import time
import pyvisa
import itertools

"""
pattern 0:  VOL+ → CH A, VOL- → CH B, AMP+ → CH C, AMP- → CH D
pattern 1:  VOL+ → CH A, VOL- → CH B, AMP+ → CH D, AMP- → CH C
pattern 2:  VOL+ → CH A, VOL- → CH C, AMP+ → CH B, AMP- → CH D
pattern 3:  VOL+ → CH A, VOL- → CH C, AMP+ → CH D, AMP- → CH B
pattern 4:  VOL+ → CH A, VOL- → CH D, AMP+ → CH B, AMP- → CH C
pattern 5:  VOL+ → CH A, VOL- → CH D, AMP+ → CH C, AMP- → CH B
pattern 6:  VOL+ → CH B, VOL- → CH A, AMP+ → CH C, AMP- → CH D
pattern 7:  VOL+ → CH B, VOL- → CH A, AMP+ → CH D, AMP- → CH C
pattern 8:  VOL+ → CH B, VOL- → CH C, AMP+ → CH A, AMP- → CH D
pattern 9:  VOL+ → CH B, VOL- → CH C, AMP+ → CH D, AMP- → CH A
pattern 10: VOL+ → CH B, VOL- → CH D, AMP+ → CH A, AMP- → CH C
pattern 11: VOL+ → CH B, VOL- → CH D, AMP+ → CH C, AMP- → CH A
pattern 12: VOL+ → CH C, VOL- → CH A, AMP+ → CH B, AMP- → CH D
pattern 13: VOL+ → CH C, VOL- → CH A, AMP+ → CH D, AMP- → CH B
pattern 14: VOL+ → CH C, VOL- → CH B, AMP+ → CH A, AMP- → CH D
pattern 15: VOL+ → CH C, VOL- → CH B, AMP+ → CH D, AMP- → CH A
pattern 16: VOL+ → CH C, VOL- → CH D, AMP+ → CH A, AMP- → CH B
pattern 17: VOL+ → CH C, VOL- → CH D, AMP+ → CH B, AMP- → CH A
pattern 18: VOL+ → CH D, VOL- → CH A, AMP+ → CH B, AMP- → CH C
pattern 19: VOL+ → CH D, VOL- → CH A, AMP+ → CH C, AMP- → CH B
pattern 20: VOL+ → CH D, VOL- → CH B, AMP+ → CH A, AMP- → CH C
pattern 21: VOL+ → CH D, VOL- → CH B, AMP+ → CH C, AMP- → CH A
pattern 22: VOL+ → CH D, VOL- → CH C, AMP+ → CH A, AMP- → CH B
pattern 23: VOL+ → CH D, VOL- → CH C, AMP+ → CH B, AMP- → CH A
"""

"""
SCPI pattern 0:
ROUTe:OPEN:ALL
ROUTe:CLOSe (@0!0)
ROUTe:CLOSe (@1!1)
ROUTe:CLOSe (@2!2)
ROUTe:CLOSe (@3!3)
"""

"""
SCPI pattern 23:
ROUTe:OPEN:ALL
ROUTe:CLOSe (@0!3)
ROUTe:CLOSe (@1!2)
ROUTe:CLOSe (@2!1)
ROUTe:CLOSe (@3!0)
"""

# Aliases for inputs (rows)
class Input:
    VOL_POSITIVE = 0   # Vol positive input
    VOL_NEGATIVE = 1   # Vol negative input
    AMP_POSITIVE = 2   # Amp positive input
    AMP_NEGATIVE = 3   # Amp negative input

# Aliases for outputs (columns)
class Output:
    CHANNEL_A = 0
    CHANNEL_B = 1
    CHANNEL_C = 2
    CHANNEL_D = 3

# Human-readable names for debugging/display
INPUT_NAMES = {
    Input.VOL_POSITIVE: "VOL+",
    Input.VOL_NEGATIVE: "VOL-",
    Input.AMP_POSITIVE: "AMP+",
    Input.AMP_NEGATIVE: "AMP-"
}

OUTPUT_NAMES = {
    Output.CHANNEL_A: "CH A",
    Output.CHANNEL_B: "CH B",
    Output.CHANNEL_C: "CH C",
    Output.CHANNEL_D: "CH D"
}

# Helper function to create channel string
def make_channel(input_num, output_num):
    """
    Create channel string in format "@X!Y"
    
    Args:
        input_num: Input number (use Input class constants)
        output_num: Output number (use Output class constants)
    
    Returns:
        String like "@0!2"
    """
    return f"@{input_num}!{output_num}"

# Helper function to parse channel string
def parse_channel(channel_str):
    """
    Parse channel string and return input and output numbers
    
    Args:
        channel_str: String like "@0!2"
    
    Returns:
        Tuple (input_num, output_num)
    """
    parts = channel_str[1:].split('!')
    return int(parts[0]), int(parts[1])

# Helper function to get human-readable channel description
def describe_channel(channel_str):
    """
    Get human-readable description of a channel
    
    Args:
        channel_str: String like "@0!2"
    
    Returns:
        String like "VOL+ -> CH C"
    """
    input_num, output_num = parse_channel(channel_str)
    input_name = INPUT_NAMES.get(input_num, f"IN{input_num}")
    output_name = OUTPUT_NAMES.get(output_num, f"OUT{output_num}")
    return f"{input_name} -> {output_name}"

class MatrixSwitchController:
    """Controller for 4x4 cross-point matrix switch with sequential connection patterns"""
    
    def __init__(self, rm=None):
        """
        Initialize the controller
        
        Args:
            rm: pyvisa ResourceManager instance (creates new if None)
        """
        self.rm = rm if rm is not None else pyvisa.ResourceManager()
        self.inst = None
        self.current_index = 0
        self.patterns = self._generate_all_patterns()
        self.callback = None
        
    def _generate_all_patterns(self):
        """
        Generate all valid connection patterns (bijections between inputs and outputs)
        
        Returns:
            List of patterns, where each pattern is a list of 4 connections in format "@X!Y"
        """
        inputs = [Input.VOL_POSITIVE, Input.VOL_NEGATIVE, Input.AMP_POSITIVE, Input.AMP_NEGATIVE]
        patterns = []
        
        for perm in itertools.permutations([Output.CHANNEL_A, Output.CHANNEL_B, 
                                            Output.CHANNEL_C, Output.CHANNEL_D]):
            # perm[i] = output connected to input i
            connections = [make_channel(inputs[i], perm[i]) for i in range(len(inputs))]
            patterns.append(connections)
            
        return patterns
    
    def connect(self):
        """Establish connection to the instrument"""
        print(f"Connecting to {env.VISA_RESOURCE_NAME}")
        self.inst = self.rm.open_resource(env.VISA_RESOURCE_NAME, read_termination="\r\n")
        self.inst.query_delay = env.VISA_QUERY_DELAY_SEC
        time.sleep(env.VISA_QUERY_DELAY_SEC)
        
    def disconnect(self):
        """Close connection to the instrument"""
        if self.inst:
            print(f"Disconnecting from {env.VISA_RESOURCE_NAME}")
            self.inst.close()
            self.inst = None
            
    def open_all_channels(self):
        """Open all channels in the matrix"""
        if not self.inst:
            raise Exception("Not connected to instrument")
        self.inst.write('ROUTe:OPEN:ALL')
        time.sleep(env.VISA_QUERY_DELAY_SEC)
        
    def set_pattern(self, pattern):
        """
        Set a specific connection pattern
        
        Args:
            pattern: List of channel connections in format "@X!Y"
        """
        if not self.inst:
            raise Exception("Not connected to instrument")
            
        # Open all channels first to ensure clean state
        self.open_all_channels()
        
        # Close each channel in the pattern
        for channel in pattern:
            self.inst.write(f'ROUTe:CLOSe ({channel})')
            
        time.sleep(env.VISA_QUERY_DELAY_SEC)
        
    def set_custom_pattern(self, connections):
        """
        Set a custom pattern using input and output aliases
        
        Args:
            connections: List of tuples (input_alias, output_alias)
                        Example: [(Input.VOL_POSITIVE, Output.CHANNEL_A),
                                  (Input.VOL_NEGATIVE, Output.CHANNEL_B),
                                  (Input.AMP_POSITIVE, Output.CHANNEL_C),
                                  (Input.AMP_NEGATIVE, Output.CHANNEL_D)]
        """
        pattern = [make_channel(inp, out) for inp, out in connections]
        self.set_pattern(pattern)
        
    def set_pattern_by_index(self, index):
        """
        Set pattern by its index in the patterns list
        
        Args:
            index: Index of the pattern to set
        """
        if index < 0 or index >= len(self.patterns):
            raise IndexError(f"Pattern index {index} out of range (0-{len(self.patterns)-1})")
            
        pattern = self.patterns[index]
        self.set_pattern(pattern)
        
        # Call the callback function if it exists
        if self.callback:
            self.callback(index, pattern)
            
    def next_pattern(self):
        """
        Move to the next pattern (cyclic)
        """
        self.current_index = (self.current_index + 1) % len(self.patterns)
        self.set_pattern_by_index(self.current_index)
        
    def previous_pattern(self):
        """
        Move to the previous pattern (cyclic)
        """
        self.current_index = (self.current_index - 1) % len(self.patterns)
        self.set_pattern_by_index(self.current_index)
        
    def run_full_cycle(self):
        """
        Run through all patterns exactly once, from current position
        Returns to the original pattern after completion
        """
        if not self.inst:
            raise Exception("Not connected to instrument")
            
        start_index = self.current_index
        total_patterns = len(self.patterns)
        
        print(f"\n=== Running full cycle of {total_patterns} patterns ===")
        
        for i in range(total_patterns):
            pattern_index = (start_index + i) % total_patterns
            print(f"\n--- Pattern {pattern_index + 1}/{total_patterns} ---")
            self.set_pattern_by_index(pattern_index)
            
        # Restore original pattern
        self.set_pattern_by_index(start_index)
        print("\n=== Full cycle completed ===")
        
    def get_current_pattern(self):
        """Get the current pattern"""
        return self.patterns[self.current_index]
        
    def get_total_patterns(self):
        """Get total number of patterns"""
        return len(self.patterns)
        
    def set_callback(self, callback_func):
        """
        Set callback function to be called after each pattern change
        
        Args:
            callback_func: Function that takes (index, pattern) as arguments
        """
        self.callback = callback_func
        
    def reorder_patterns(self, new_order):
        """
        Reorder the patterns list
        
        Args:
            new_order: List of pattern indices in the desired order
        """
        if len(new_order) != len(self.patterns):
            raise ValueError(f"New order must have {len(self.patterns)} elements")
            
        if set(new_order) != set(range(len(self.patterns))):
            raise ValueError("New order must contain all pattern indices exactly once")
            
        self.patterns = [self.patterns[i] for i in new_order]
        # Reset current index if it's out of range
        if self.current_index >= len(self.patterns):
            self.current_index = 0
            
    def get_patterns_list(self):
        """
        Get the list of all patterns (for inspection or reordering)
        
        Returns:
            List of pattern lists
        """
        return self.patterns.copy()
        
    def print_pattern(self, pattern):
        """Pretty print a pattern with human-readable names"""
        descriptions = []
        for channel in pattern:
            descriptions.append(describe_channel(channel))
        return ", ".join(descriptions)
        
    def print_current_pattern(self):
        """Print the current pattern in human-readable format"""
        pattern = self.get_current_pattern()
        print(f"Current pattern: {self.print_pattern(pattern)}")


class CustomPatternController:
    """Controller for managing custom pattern sequences"""
    
    def __init__(self, matrix_controller):
        """
        Initialize custom pattern controller
        
        Args:
            matrix_controller: Instance of MatrixSwitchController
        """
        self.matrix = matrix_controller
        self.pattern_sequence = []
        self.current_seq_index = 0
        self.pattern_names = {}  # Optional: store names for patterns
        
    def define_sequence(self, patterns, names=None):
        """
        Define the pattern sequence
        
        Args:
            patterns: List of patterns, each pattern is a list of tuples 
                     (input_alias, output_alias)
            names: Optional list of names for each pattern
        """
        self.pattern_sequence = patterns
        self.current_seq_index = 0
        
        if names:
            if len(names) != len(patterns):
                print("Warning: Number of names doesn't match number of patterns")
            else:
                self.pattern_names = {i: names[i] for i in range(len(patterns))}
        else:
            self.pattern_names = {}
            
        print(f"Sequence defined with {len(patterns)} patterns")
        
    def add_pattern(self, pattern, name=None):
        """
        Add a pattern to the end of the sequence
        
        Args:
            pattern: Pattern as list of tuples (input_alias, output_alias)
            name: Optional name for the pattern
        """
        index = len(self.pattern_sequence)
        self.pattern_sequence.append(pattern)
        if name:
            self.pattern_names[index] = name
        print(f"Pattern {index} added to sequence")
        
    def insert_pattern(self, index, pattern, name=None):
        """
        Insert a pattern at specific position
        
        Args:
            index: Position to insert at
            pattern: Pattern as list of tuples (input_alias, output_alias)
            name: Optional name for the pattern
        """
        if 0 <= index <= len(self.pattern_sequence):
            self.pattern_sequence.insert(index, pattern)
            # Update pattern names
            new_names = {}
            for i, (old_idx, name_val) in enumerate(self.pattern_names.items()):
                if i < index:
                    new_names[i] = name_val
                else:
                    new_names[i + 1] = name_val
            if name:
                new_names[index] = name
            self.pattern_names = new_names
            print(f"Pattern inserted at position {index}")
        else:
            print(f"Index {index} out of range")
        
    def remove_pattern(self, index):
        """
        Remove pattern at specific position
        
        Args:
            index: Position to remove
        """
        if 0 <= index < len(self.pattern_sequence):
            removed = self.pattern_sequence.pop(index)
            # Update pattern names
            new_names = {}
            for i, (old_idx, name_val) in enumerate(self.pattern_names.items()):
                if i < index:
                    new_names[i] = name_val
                elif i > index:
                    new_names[i - 1] = name_val
            self.pattern_names = new_names
            print(f"Pattern {index} removed from sequence")
            return removed
        else:
            print(f"Index {index} out of range")
            return None
        
    def set_next_pattern(self):
        """Set the next pattern from the sequence (cyclic)"""
        if not self.pattern_sequence:
            print("No patterns defined in sequence")
            return False
            
        pattern = self.pattern_sequence[self.current_seq_index]
        
        # Print pattern info if available
        if self.current_seq_index in self.pattern_names:
            print(f"\n--- Setting pattern {self.current_seq_index + 1}: {self.pattern_names[self.current_seq_index]} ---")
        else:
            print(f"\n--- Setting pattern {self.current_seq_index + 1}/{len(self.pattern_sequence)} ---")
        
        self.matrix.set_custom_pattern(pattern)
        
        # Move to next index (cyclic)
        self.current_seq_index = (self.current_seq_index + 1) % len(self.pattern_sequence)
        return True
        
    def set_previous_pattern(self):
        """Set the previous pattern from the sequence (cyclic)"""
        if not self.pattern_sequence:
            print("No patterns defined in sequence")
            return False
            
        # Move to previous index (cyclic)
        self.current_seq_index = (self.current_seq_index - 1) % len(self.pattern_sequence)
        pattern = self.pattern_sequence[self.current_seq_index]
        
        # Print pattern info if available
        if self.current_seq_index in self.pattern_names:
            print(f"\n--- Setting pattern {self.current_seq_index + 1}: {self.pattern_names[self.current_seq_index]} ---")
        else:
            print(f"\n--- Setting pattern {self.current_seq_index + 1}/{len(self.pattern_sequence)} ---")
        
        self.matrix.set_custom_pattern(pattern)
        return True
        
    def set_pattern_by_seq_index(self, index):
        """
        Set pattern by index in the sequence
        
        Args:
            index: 0-based index in the sequence
        """
        if 0 <= index < len(self.pattern_sequence):
            self.current_seq_index = index
            pattern = self.pattern_sequence[self.current_seq_index]
            
            # Print pattern info if available
            if self.current_seq_index in self.pattern_names:
                print(f"\n--- Setting pattern {self.current_seq_index + 1}: {self.pattern_names[self.current_seq_index]} ---")
            else:
                print(f"\n--- Setting pattern {self.current_seq_index + 1}/{len(self.pattern_sequence)} ---")
            
            self.matrix.set_custom_pattern(pattern)
        else:
            print(f"Index {index} out of range (0-{len(self.pattern_sequence)-1})")
            
    def get_current_pattern_info(self):
        """Get information about current pattern"""
        if not self.pattern_sequence:
            return None
            
        pattern = self.pattern_sequence[self.current_seq_index]
        info = {
            'index': self.current_seq_index,
            'pattern': pattern,
            'total': len(self.pattern_sequence)
        }
        
        if self.current_seq_index in self.pattern_names:
            info['name'] = self.pattern_names[self.current_seq_index]
            
        return info
        
    def run_full_sequence(self, repeat=False, delay_between=0.5):
        """
        Run through the entire sequence
        
        Args:
            repeat: If True, continue cycling through patterns
            delay_between: Delay in seconds between pattern changes
        """
        if not self.pattern_sequence:
            print("No patterns defined in sequence")
            return
            
        total = len(self.pattern_sequence)
        start_index = self.current_seq_index
        
        print(f"\n=== Running sequence of {total} patterns ===")
        
        if repeat:
            print("Mode: Cyclic (will repeat continuously)")
        
        # Run the sequence
        for i in range(total if not repeat else total * 10):  # Limit to 10 cycles if repeating
            pattern_index = (start_index + i) % total
            print(f"\n--- Pattern {pattern_index + 1}/{total} ---")
            
            if pattern_index in self.pattern_names:
                print(f"Name: {self.pattern_names[pattern_index]}")
                
            pattern = self.pattern_sequence[pattern_index]
            self.matrix.set_custom_pattern(pattern)
            
            if i < (total if not repeat else total * 10) - 1:
                time.sleep(delay_between)
                
        if not repeat:
            # Return to original pattern
            print(f"\n--- Returning to pattern {start_index + 1} ---")
            self.matrix.set_custom_pattern(self.pattern_sequence[start_index])
            self.current_seq_index = start_index
            print("\n=== Sequence completed, returned to start ===")
        else:
            print("\n=== Sequence completed ===")
            
    def clear_sequence(self):
        """Clear all patterns from the sequence"""
        self.pattern_sequence = []
        self.current_seq_index = 0
        self.pattern_names = {}
        print("Sequence cleared")
        
    def get_sequence_info(self):
        """Print information about the current sequence"""
        if not self.pattern_sequence:
            print("No patterns defined in sequence")
            return
            
        print(f"\n=== Sequence Info ===")
        print(f"Total patterns: {len(self.pattern_sequence)}")
        print(f"Current position: {self.current_seq_index + 1}")
        
        for i, pattern in enumerate(self.pattern_sequence):
            name = self.pattern_names.get(i, f"Pattern {i+1}")
            # Get first and last connection for summary
            first_conn = describe_channel(make_channel(pattern[0][0], pattern[0][1]))
            last_conn = describe_channel(make_channel(pattern[-1][0], pattern[-1][1]))
            print(f"  {i+1}: {name} - {first_conn}, {last_conn} ...")


def generate_all_patterns():
    """
    Generate all 24 valid connection patterns (bijections)
    
    Returns:
        List of all patterns, each pattern is a list of tuples (input, output)
        and list of corresponding names
    """
    inputs = [Input.VOL_POSITIVE, Input.VOL_NEGATIVE, Input.AMP_POSITIVE, Input.AMP_NEGATIVE]
    outputs = [Output.CHANNEL_A, Output.CHANNEL_B, Output.CHANNEL_C, Output.CHANNEL_D]
    
    patterns = []
    pattern_names = []
    
    pattern_counter = 1
    for perm in itertools.permutations(outputs):
        # Create pattern as list of tuples
        pattern = [(inputs[i], perm[i]) for i in range(len(inputs))]
        patterns.append(pattern)
        
        # Create a descriptive name for the pattern
        connections = []
        for i in range(len(inputs)):
            input_name = INPUT_NAMES[inputs[i]]
            output_name = OUTPUT_NAMES[perm[i]]
            connections.append(f"{input_name}→{output_name}")
        pattern_names.append(f"P{pattern_counter:02d}: " + ", ".join(connections))
        pattern_counter += 1
        
    return patterns, pattern_names


# Example callback function
def on_pattern_change(pattern_index, pattern):
    """
    Callback function called after each pattern change
    
    Args:
        pattern_index: Index of the current pattern
        pattern: List of channel connections in format "@X!Y"
    """
    print(f"\n=== Callback: Pattern {pattern_index} changed ===")
    for channel in pattern:
        print(f"  {describe_channel(channel)}")


def main():
    """Main function demonstrating the custom pattern controller with all 24 patterns"""
    rm = pyvisa.ResourceManager()
    controller = MatrixSwitchController(rm)
    custom_controller = CustomPatternController(controller)
    
    try:
        # Connect to the instrument
        controller.connect()
        
        # Set callback (optional)
        controller.set_callback(on_pattern_change)
        
        # Generate all 24 patterns
        print("Generating all 24 valid connection patterns...")
        all_patterns, pattern_names = generate_all_patterns()
        
        print(f"Total patterns generated: {len(all_patterns)}")
        
        # Load all patterns into the sequence
        custom_controller.define_sequence(all_patterns, pattern_names)
        
        # Display sequence info
        custom_controller.get_sequence_info()
        
        # Demo 1: Step-by-step navigation through first few patterns
        print("\n" + "="*50)
        print("Demo 1: Step-by-step navigation (first 5 patterns)")
        print("="*50)
        
        for i in range(5):
            custom_controller.set_next_pattern()
            time.sleep(1)
        
        # Demo 2: Run full sequence of all 24 patterns once
        print("\n" + "="*50)
        print("Demo 2: Run full sequence of all 24 patterns once")
        print("="*50)
        
        # Reset to beginning
        custom_controller.set_pattern_by_seq_index(0)
        time.sleep(0.5)
        
        # Run all 24 patterns
        custom_controller.run_full_sequence(repeat=False, delay_between=0.3)
        
        # Demo 3: Run a subset of patterns
        print("\n" + "="*50)
        print("Demo 3: Run specific patterns by index")
        print("="*50)
        
        # Run specific patterns of interest
        interesting_indices = [0, 6, 12, 18, 23]  # Example: every 6th pattern
        for idx in interesting_indices:
            print(f"\n--- Setting pattern {idx + 1} ---")
            custom_controller.set_pattern_by_seq_index(idx)
            time.sleep(1)
        
        print("\n" + "="*50)
        print("All demos completed")
        print("="*50)
        
        time.sleep(1)
        
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        # Clean up
        controller.disconnect()


def interactive_mode():
    """Interactive mode for manual control with all 24 patterns"""
    rm = pyvisa.ResourceManager()
    controller = MatrixSwitchController(rm)
    custom_controller = CustomPatternController(controller)
    
    # Generate all 24 patterns
    all_patterns, pattern_names = generate_all_patterns()
    custom_controller.define_sequence(all_patterns, pattern_names)
    
    try:
        controller.connect()
        
        print("\n" + "="*60)
        print("Interactive Mode with ALL 24 Valid Connection Patterns")
        print("="*60)
        print("Commands:")
        print("  n - Next pattern (cyclic through all 24 patterns)")
        print("  p - Previous pattern (cyclic)")
        print("  1-24 - Set specific pattern by number")
        print("  i - Show current pattern info")
        print("  l - List all patterns (first few shown)")
        print("  r - Run full sequence of all 24 patterns once")
        print("  c - Run cyclic sequence (press Ctrl+C to stop)")
        print("  q - Quit")
        print("="*60)
        
        running = True
        while running:
            cmd = input("\nEnter command: ").strip().lower()
            
            if cmd == 'q':
                running = False
                print("Exiting...")
                
            elif cmd == 'n':
                custom_controller.set_next_pattern()
                
            elif cmd == 'p':
                custom_controller.set_previous_pattern()
                
            elif cmd.isdigit():
                num = int(cmd)
                if 1 <= num <= 24:
                    custom_controller.set_pattern_by_seq_index(num - 1)
                else:
                    print("Please enter a number between 1 and 24")
                
            elif cmd == 'i':
                info = custom_controller.get_current_pattern_info()
                if info:
                    print(f"\nCurrent pattern: {info['index'] + 1}/{info['total']}")
                    if 'name' in info:
                        print(f"Name: {info['name']}")
                    print("Connections:")
                    for inp, out in info['pattern']:
                        print(f"  {INPUT_NAMES[inp]} -> {OUTPUT_NAMES[out]}")
                        
            elif cmd == 'l':
                print("\n=== Available Patterns (first 8 shown) ===")
                for i in range(min(8, len(pattern_names))):
                    print(f"  {i+1}: {pattern_names[i]}")
                if len(pattern_names) > 8:
                    print(f"  ... and {len(pattern_names) - 8} more patterns")
                print("Use '1-24' to set specific pattern")
                
            elif cmd == 'r':
                print("\nRunning full sequence of all 24 patterns...")
                custom_controller.run_full_sequence(repeat=False, delay_between=0.3)
                
            elif cmd == 'c':
                print("Running cyclic sequence (press Ctrl+C to stop)...")
                try:
                    custom_controller.run_full_sequence(repeat=True, delay_between=0.3)
                except KeyboardInterrupt:
                    print("\nCyclic sequence stopped")
                    
            else:
                print("Unknown command. Available: n, p, 1-24, i, l, r, c, q")
                
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        controller.disconnect()


def run_specific_sequence():
    """
    Run a custom sequence of your choice.
    You can modify this function to create any order of patterns you want.
    """
    rm = pyvisa.ResourceManager()
    controller = MatrixSwitchController(rm)
    custom_controller = CustomPatternController(controller)
    
    # Generate all patterns
    all_patterns, pattern_names = generate_all_patterns()
    
    # Create your custom order by specifying pattern indices (0-23)
    # Example: Run patterns in a specific sequence
    custom_order_indices = [
        0,   # Pattern 0
        1,   # Pattern 1
        2,   # Pattern 2
        3,   # Pattern 3
        12,  # Pattern 12
        13,  # Pattern 13
        23,  # Pattern 23
        22,  # Pattern 22
        21,  # Pattern 21
        11,  # Pattern 11
        10,  # Pattern 10
        9    # Pattern 9
    ]
    
    # Create sequence based on custom order
    custom_sequence = [all_patterns[i] for i in custom_order_indices]
    custom_names = [pattern_names[i] for i in custom_order_indices]
    
    try:
        controller.connect()
        custom_controller.define_sequence(custom_sequence, custom_names)
        
        print(f"\n=== Running custom sequence of {len(custom_sequence)} patterns ===")
        custom_controller.get_sequence_info()
        
        # Run the custom sequence
        custom_controller.run_full_sequence(repeat=False, delay_between=0.5)
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        controller.disconnect()


if __name__ == '__main__':
    # Choose which mode to run:
    main()  # Demo mode with all 24 patterns
    # interactive_mode()  # Interactive mode with all 24 patterns
    # run_specific_sequence()  # Run a custom sequence
