package org.example.ticket.performance.domain;

import jakarta.persistence.*;
import lombok.*;
import org.example.ticket.venue.domain.VenueHall;

import java.time.LocalDateTime;

@Entity
@Table(name = "performance_time")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class PerformanceTime {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "performance_id", nullable = false)
    private Performance performance;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "venue_hall_id", nullable = false)
    private VenueHall venueHall;
    @Column(name = "starts_at", nullable = false)
    private LocalDateTime startsAt;
}
