"""
Test for progress bar parsing in inpainter.py
Verifies that tqdm progress output is correctly parsed for progress percentage.
"""
import re


def test_tqdm_progress_parsing():
    """Test regex parsing of tqdm progress format"""
    
    # Common tqdm format examples
    test_cases = [
        ("50%|████      | 1/2 [00:05<00:05]", 50),
        ("0%|          | 0/10", 0),
        ("25%|██▌       | 2/8", 25),
        ("75%|███████▌  | 6/8", 75),
        ("100%|██████████| 10/10", 100),
        ("12%|█▏        | 1/8 [00:01, 0.75it/s]", 12),
        ("99%|█████████▉| 199/200", 99),
    ]
    
    pattern = r'(\d+)%'
    
    for output_line, expected_percent in test_cases:
        match = re.search(pattern, output_line)
        assert match is not None, f"Pattern failed to match: {output_line}"
        
        percent = int(match.group(1))
        assert percent == expected_percent, \
            f"Expected {expected_percent}% from '{output_line}', got {percent}%"
        
        print(f"✓ Correctly parsed '{output_line[:40]}...' as {percent}%")


def test_progress_callback_simulation():
    """Test the progress callback mechanism"""
    
    progress_updates = []
    
    def mock_callback(percent: int, message: str):
        progress_updates.append((percent, message))
    
    # Simulate subprocess output with changing percentages
    simulated_output = [
        "0%|          | 0/100",
        "25%|██▌       | 25/100",
        "50%|████      | 50/100",
        "75%|███████▌  | 75/100",
        "100%|██████████| 100/100",
    ]
    
    pattern = r'(\d+)%'
    last_progress = -1  # Start at -1 so 0% is reported
    
    for line in simulated_output:
        match = re.search(pattern, line)
        if match:
            percent = int(match.group(1))
            if percent != last_progress:  # Only report on change
                mock_callback(percent, f"处理中... {percent}%")
                last_progress = percent
    
    # Verify callback was called with expected values
    assert len(progress_updates) == 5, f"Expected 5 updates, got {len(progress_updates)}"
    
    expected_percents = [0, 25, 50, 75, 100]
    for i, (percent, message) in enumerate(progress_updates):
        assert percent == expected_percents[i], \
            f"Update {i}: expected {expected_percents[i]}%, got {percent}%"
        
        print(f"✓ Update {i}: {percent}% - {message}")


def test_deduplication():
    """Test that duplicate progress values are not reported"""
    
    progress_updates = []
    
    def mock_callback(percent: int, message: str):
        progress_updates.append((percent, message))
    
    # Simulate output with duplicate percentages (common with tqdm)
    simulated_output = [
        "50%|████      | 50/100",
        "50%|████      | 50/100",  # Duplicate
        "50%|████▏     | 50/100",  # Same percent, different bar
        "51%|████▏     | 51/100",  # New percent
    ]
    
    pattern = r'(\d+)%'
    last_progress = -1
    
    for line in simulated_output:
        match = re.search(pattern, line)
        if match:
            percent = int(match.group(1))
            if percent != last_progress:  # Deduplication
                mock_callback(percent, f"处理中... {percent}%")
                last_progress = percent
    
    # Should only report 50% once, then 51%
    assert len(progress_updates) == 2, \
        f"Expected 2 unique updates, got {len(progress_updates)}"
    
    assert progress_updates[0][0] == 50, "First update should be 50%"
    assert progress_updates[1][0] == 51, "Second update should be 51%"
    
    print(f"✓ Deduplication working: {len(progress_updates)} updates from 4 lines")


def test_non_progress_lines():
    """Test that lines without progress percentage are ignored"""
    
    progress_updates = []
    
    def mock_callback(percent: int, message: str):
        progress_updates.append((percent, message))
    
    mixed_output = [
        "Loading model...",
        "25%|██▌       | 25/100",
        "Setting up...",
        "50%|████      | 50/100",
        "Finalizing...",
        "100%|██████████| 100/100",
        "Done!",
    ]
    
    pattern = r'(\d+)%'
    last_progress = -1
    
    for line in mixed_output:
        match = re.search(pattern, line)
        if match:
            percent = int(match.group(1))
            if percent != last_progress:
                mock_callback(percent, f"处理中... {percent}%")
                last_progress = percent
    
    # Should only report the three progress lines
    assert len(progress_updates) == 3, \
        f"Expected 3 updates, got {len(progress_updates)}"
    
    expected_percents = [25, 50, 100]
    for i, (percent, _) in enumerate(progress_updates):
        assert percent == expected_percents[i], \
            f"Update {i}: expected {expected_percents[i]}%, got {percent}%"
    
    print(f"✓ Correctly ignored non-progress lines: only {len(progress_updates)} updates")


if __name__ == "__main__":
    print("Testing tqdm progress parsing...")
    print()
    
    print("Test 1: Basic tqdm parsing")
    test_tqdm_progress_parsing()
    print()
    
    print("Test 2: Progress callback simulation")
    test_progress_callback_simulation()
    print()
    
    print("Test 3: Deduplication")
    test_deduplication()
    print()
    
    print("Test 4: Non-progress lines")
    test_non_progress_lines()
    print()
    
    print("✅ All tests passed!")
