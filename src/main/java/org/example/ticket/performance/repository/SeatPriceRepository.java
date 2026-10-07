package org.example.ticket.performance.repository;
import org.example.ticket.performance.domain.SeatPrice;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
public interface SeatPriceRepository extends JpaRepository<SeatPrice, Long> {
    List<SeatPrice> findAllByPerformanceId(Long performanceId);
}
