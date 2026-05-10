#!/usr/bin/env python3
"""
Memory Routing System Health Check Tool
========================================
This tool checks the health status of the memory routing system
and provides comprehensive health reports.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


class HealthCheck:
    """Comprehensive health check for memory routing system"""
    
    def __init__(self, base_path="~/.hermes/profiles/nova"):
        self.base_path = Path(base_path).expanduser()
        self.memory_dir = self.base_path / "memory"
        self.health_score = 100
        self.issues = []
        
    def check_file_permissions(self):
        """Check file permissions for security"""
        # Check memory files
        if self.memory_dir.exists():
            for file in self.memory_dir.glob("*.md"):
                permissions = oct(file.stat().st_mode)[-3:]
                if permissions != "600":
                    self.health_score -= 5
                    self.issues.append(f"Incorrect permissions for {file.name}")
        
        # Check credentials
        cred_file = self.base_path / "CREDENTIALS.md"
        if cred_file.exists():
            permissions = oct(cred_file.stat().st_mode)[-3:]
            if permissions != "600":
                self.health_score -= 10
                self.issues.append("Credentials file permissions not 600")
                
    def check_storage_usage(self):
        """Check memory storage usage"""
        memory_size = os.path.getsize(self.base_path / "MEMORY.md")
        max_size = 10000
        usage = memory_size / max_size * 100
        
        if usage > 90:
            self.health_score -= 20
            self.issues.append("Memory storage usage exceeds 90%")
        elif usage > 80:
            self.health_score -= 10
            self.issues.append("Memory storage usage exceeds 80%")
            
    def check_audit_health(self):
        """Check audit system health"""
        audit_file = self.memory_dir / ".audit.jsonl"
        if not audit_file.exists():
            self.health_score -= 15
            self.issues.append("Audit trail file missing")
            return
            
        # Check recent entries
        with open(audit_file, 'r') as f:
            lines = f.readlines()
        
        entries = [json.loads(line) for line in lines]
        
        # Check for errors
        errors = len([e for e in entries if "error" in str(e).lower()])
        if errors > 0:
            self.health_score -= errors * 2
            self.issues.append(f"Found {errors} errors in audit trail")
            
    def check_subdocument_health(self):
        """Check health of all sub-documents"""
        subdocs = [
            "infrastructure.md",
            "philosophy.md", 
            "dev-log.md",
            "hell-contract.md",
            "commitments.md",
            "rules.md",
            "milestones.md"
        ]
        
        for doc in subdocs:
            doc_path = self.memory_dir / doc
            if not doc_path.exists():
                self.health_score -= 10
                self.issues.append(f"Sub-document {doc} missing")
            else:
                # Check for recent activity
                with open(doc_path, 'r', errors='ignore') as f:
                    content = f.read()
                if "2026-05-" not in content:
                    self.issues.append(f"{doc} has no recent activity")
                    
    def check_fact_cache(self):
        """Check fact cache health"""
        fact_cache = self.memory_dir / ".fact_cache.json"
        if not fact_cache.exists():
            self.health_score -= 5
            self.issues.append("Fact cache file missing")
            return
            
        try:
            with open(fact_cache, 'r') as f:
                cache = json.load(f)
                
            facts = cache.get('facts', [])
            
            # Check for corrupted facts
            corrupt_facts = [f for f in facts if not isinstance(f, dict) or 'category' not in f]
            if corrupt_facts:
                self.health_score -= 5
                self.issues.append(f"Found {len(corrupt_facts)} corrupted facts")
        except Exception as e:
            self.health_score -= 10
            self.issues.append(f"Failed to load fact cache: {str(e)}")
            
    def generate_health_report(self):
        """Generate comprehensive health report"""
        return {
            "timestamp": datetime.now().isoformat(),
            "health_score": self.health_score,
            "issues": self.issues,
            "checks": {
                "file_permissions": self.check_file_permissions(),
                "storage_usage": self.check_storage_usage(),
                "audit_health": self.check_audit_health(),
                "subdocument_health": self.check_subdocument_health(),
                "fact_cache": self.check_fact_cache()
            }
        }


def main():
    """Main function to run health checks"""
    check = HealthCheck()
    report = check.generate_health_report()
    
    # Print report
    print("=" * 80)
    print("Memory Routing System Health Check Report")
    print("=" * 80)
    print(f"Timestamp: {report['timestamp']}")
    print(f"Health Score: {report['health_score']}/100")
    print(f"Issues Found: {len(report['issues'])}")
    
    for issue in report['issues']:
        print(f"  - {issue}")
    
    return 0 if report['health_score'] >= 70 else 1


if __name__ == "__main__":
    sys.exit(main())
