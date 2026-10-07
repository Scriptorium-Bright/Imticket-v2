package org.example.ticket.performance.repository;
import org.example.ticket.performance.domain.Performance;
import org.springframework.data.jpa.repository.JpaRepository;
public interface PerformanceRepository extends JpaRepository<Performance, Long> {}
