#!/usr/bin/env python3
"""
Audit Trail Analyzer
===================
Analyzes audit trail data and provides insights into memory routing performance
"""

import json
import os
import sys
from datetime import datetime
from collections import Counter


class AuditAnalyzer:
    """Analyze audit trail data"""
    
    def __init__(self, base_path="~/.hermes/profiles/nova"):
        self.base_path = base_path
        self.audit_file = f"{base_path}/memory/.audit.jsonl"
        
    def load_recent_entries(self, days=1):
        """Load recent audit entries"""
        with open(self.audit_file, 'r') as f:
            return [json.loads(line) for line in f.readlines()[-days*24:]]
            
    def analyze_routing_paths(self, entries):
        """Analyze routing path distribution"""
        paths = Counter()
        for entry in entries:
            score = entry.get('score', 0)
            if score >= 3:
                paths['Fast Path'] += 1
            elif score >= 1:
                paths['LLM Review'] += 1
            else:
                paths['Fallback'] += 1
        return paths
        
    def analyze_performance(self, entries):
        """Analyze performance metrics"""
        total = len(entries)
        if total == 0:
            return {}
            
        fast_path = len([e for e in entries if e.get('score', 0) >= 3])
        return {
            "total_entries": total,
            "fast_path_rate": fast_path / total * 100,
            "llm_review_rate": len([e for e in entries if 1 <= e.get('score', 0) < 3]) / total * 100,
            "fallback_rate": len([e for e in entries if e.get('score', 0) == 0]) / total * 100
        }
        
    def analyze_errors(self, entries):
        """Analyze error patterns"""
        error_count = 0
        error_types = Counter()
        for entry in entries:
            if "error" in str(entry).lower():
                error_count += 1
                error_types[entry.get('type', 'unknown')] += 1
        return {"error_count": error_count, "error_types": error_types}
    
    def generate_report(self, days=1):
        """Generate comprehensive audit report"""
        entries = self.load_recent_entries(days)
        
        return {
            "analysis_period": days,
            "entry_count": len(entries),
            "routing_analysis": self.analyze_routing_paths(entries),
            "performance_metrics": self.analyze_performance(entries),
            "error_analysis": self.analyze_errors(entries)
        }


def main():
    """Main function to analyze audit trails"""
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    
    analyzer = AuditAnalyzer()
    report = analyzer.generate_report(days)
    
    # Print report
    print("=" * 80)
    print("Audit Trail Analysis Report")
    print("=" * 80)
    print(f"Analysis Period: {days} days")
    print(f"Total Entries: {report['entry_count']}")
    
    print("
Routing Path Distribution:")
    for path, count in report['routing_analysis'].items():
        print(f"  {path}: {count} ({count/report['entry_count']*100:.1f}%)")
    
    print("
Performance Metrics:")
    for metric, value in report['performance_metrics'].items():
        print(f"  {metric}: {value}")
    
    print("
Error Analysis:")
    errors = report['error_analysis']
    print(f"  Total Errors: {errors['error_count']}")
    print(f"  Error Types: {errors['error_types']}")


if __name__ == "__main__":
    main()
